#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pearl 守护器 — SRBMiner 看门狗 + 状态面板

解决的问题：
  1. SRBMiner 控制台窗口被 QuickEdit 选中后停止刷新（要点窗口敲回车）
     → 本程序用 --background 启动，完全不要控制台窗口，改为自己做 UI
  2. 矿工假死/退出无人发现
     → 每 5 秒读一次 SRBMiner 统计 API，进程消失、API 连续超时、
       算力长时间为 0，都会自动杀掉重启

依赖：PyQt6（仅此一个）
"""

from __future__ import annotations

import json
import ctypes
from ctypes import wintypes
import os
from queue import Empty, SimpleQueue
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from zipfile import BadZipFile

from miner_update import CHECK_INTERVAL_MS, MinerInstall, Release, binary_version, check_release, version_key
from gpu_info import detect, worker_from_model

from PyQt6.QtCore import (
    QAbstractNativeEventFilter,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    QTime,
    Qt,
    QThread,
    QTimer,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QScrollArea,
    QSystemTrayIcon,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

CREATE_NO_WINDOW = 0x08000000

# 可能出现的计算引擎进程镜像名（SRBMiner 为主，krig 兼容旧方案）
MINE_IMAGES = ("SRBMiner-MULTI.exe", "krig-miner.exe")


def list_miner_pids() -> list[int]:
    """列出所有算力引擎进程 PID。"""
    pids: list[int] = []
    for image in MINE_IMAGES:
        try:
            r = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                encoding="gbk",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
                timeout=10,
            )
            for line in (r.stdout or "").splitlines():
                parts = [x.strip('"') for x in line.split('","')]
                if len(parts) >= 2 and parts[0].lower() == image.lower():
                    try:
                        pids.append(int(parts[1]))
                    except ValueError:
                        pass
        except Exception:
            continue
    return pids


def kill_miners(keep_pid: int | None = None) -> int:
    """杀掉所有算力引擎进程（可保留一个），返回杀掉的数量。"""
    killed = 0
    for pid in list_miner_pids():
        if keep_pid is not None and pid == keep_pid:
            continue
        try:
            r = subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True,
                text=True,
                encoding="gbk",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
                timeout=10,
            )
            if r.returncode == 0:
                killed += 1
        except Exception:
            continue
    return killed
APP_VERSION = "2.3.2"
APP_NAME = "Pearl 守护器"
WINDOW_TITLE = f"{APP_NAME} V{APP_VERSION}"


# ══════════════════════════════════════════════════════════════════════
# 路径与配置
# ══════════════════════════════════════════════════════════════════════
def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


CONFIG_FILE = app_dir() / "supervisor.json"

DEFAULT_CONFIG = {
    "miner_exe": "miner\\SRBMiner-MULTI.exe",
    "pool": "prl.kryptex.network:8048",
    "account": "",
    "mining_authorized": False,
    "miner_auto_update": False,
    "miner_update_proxy": "",
    "worker": "",
    "api_port": 21550,
    "fan": 0,
    "power_limit": 0,
    "global_hotkey": True,
    "close_to_tray": True,
    "core_offset": 0,
    "mem_offset": 0,
    "extra_args": "",
    "sched_enable": False,
    "sched_start": "07:00",
    "sched_stop": "23:00",
    "check_interval": 5,
    "restart_delay": 5,
    "api_fail_limit": 2,
    "zero_hash_limit": 60,
}


def resolve_path(value: str | os.PathLike[str]) -> Path:
    """安装版中的相对路径始终相对于程序目录，而不是当前工作目录。"""
    p = Path(value)
    return p if p.is_absolute() else app_dir() / p


def _normalize_worker(name: str) -> str:
    """矿工名只保留显卡/电脑型号需要的 ASCII 字符。"""
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(name or "").strip()).strip("-._")
    return text[:40] or "PearlRig"


def detect_gpu() -> dict:
    """通过独立模块读取显卡，便于安装器和守护器复用同一结果。"""
    return detect()


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            cfg.update(data)
            # 旧配置里的 12 是误判较多的老阈值，迁移到 60
            try:
                if int(cfg.get("zero_hash_limit", 60)) == 12:
                    cfg["zero_hash_limit"] = 60
            except (TypeError, ValueError):
                pass
            # 老版本只有 wallet（账户/矿工名），拆成两个字段
            if "wallet" in data and "account" not in data:
                base, _, wk = str(data["wallet"]).partition("/")
                cfg["account"] = base
                cfg["worker"] = wk or cfg.get("worker", "")
            if "mining_authorized" not in data:
                # 旧版已改用自有账户的安装继续沿用原来的自动启动。
                account = str(cfg["account"] or "").partition("/")[0].strip()
                cfg["mining_authorized"] = bool(account and account.casefold() != "krxx72p2ze")
    except Exception:
        pass
    if not str(cfg.get("worker", "")).strip():
        cfg["worker"] = detect_gpu()["worker"]
    # 旧版在本机写死的绝对路径迁移为随包矿工路径。
    if not resolve_path(cfg.get("miner_exe", "")).is_file():
        cfg["miner_exe"] = "miner\\SRBMiner-MULTI.exe"
    return cfg


def save_config(cfg: dict) -> None:
    try:
        pending = CONFIG_FILE.with_suffix(".json.tmp")
        pending.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        pending.replace(CONFIG_FILE)
    except OSError as exc:
        raise OSError(f"无法保存设置：{exc}") from exc


class GlobalHotkey(QAbstractNativeEventFilter):
    """Windows 原生热键，窗口隐藏或其他程序有焦点时同样生效。"""
    HOTKEY_ID = 0x5047

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.registered = False
        self.user32 = None
        if sys.platform == "win32":
            self.user32 = ctypes.WinDLL("user32", use_last_error=True)
            self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            self.user32.RegisterHotKey.restype = wintypes.BOOL
            self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            self.user32.UnregisterHotKey.restype = wintypes.BOOL

    def set_enabled(self, enabled: bool) -> bool:
        if self.registered == enabled:
            return True
        if self.registered:
            self.user32.UnregisterHotKey(int(self.window.winId()), self.HOTKEY_ID)
            self.registered = False
        if enabled and self.user32 is not None:
            # MOD_ALT | MOD_NOREPEAT；0x30 是主键盘数字 0。
            self.registered = bool(self.user32.RegisterHotKey(
                int(self.window.winId()), self.HOTKEY_ID, 0x0001 | 0x4000, 0x30
            ))
        return self.registered == enabled

    def nativeEventFilter(self, event_type, message):
        if self.registered and bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam == self.HOTKEY_ID:
                QTimer.singleShot(0, self.window._toggle_tray)
                return True, 0
        return False, 0


def make_app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#0A84FF"))
    painter.drawEllipse(3, 3, 58, 58)
    painter.setPen(QColor("#FFFFFF"))
    painter.setFont(QFont("Segoe UI", 32, QFont.Weight.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "P")
    painter.end()
    return QIcon(pixmap)


def query_power_range() -> tuple[int, int] | None:
    """只读取显卡范围，不尝试更改功耗。"""
    try:
        result = subprocess.run(
            ["nvidia-smi", "-i", "0", "--query-gpu=power.min_limit,power.max_limit",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, creationflags=CREATE_NO_WINDOW,
        )
        values = [float(v.strip()) for v in result.stdout.strip().splitlines()[0].split(",")]
        if result.returncode == 0 and len(values) == 2 and 0 < values[0] <= values[1]:
            return int(values[0]), int(values[1])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        pass
    return None


def discover_miner() -> str | None:
    """在常见位置找一个 SRBMiner-MULTI.exe。"""
    roots = [
        app_dir(),
        app_dir() / "miner",
        app_dir().parent / "srbminer-3.8.0" / "SRBMiner-Multi-3-8-0",
        app_dir().parent,
        Path(r"C:\Mining"),
        Path.home() / "Desktop",
        Path.home() / "Downloads",
    ]
    seen: set[str] = set()
    for root in roots:
        try:
            if not root.exists():
                continue
            for p in root.rglob("SRBMiner-MULTI.exe"):
                key = str(p).lower()
                if key in seen or not p.is_file():
                    continue
                seen.add(key)
                return str(p)
        except Exception:
            continue
    return None


def pick_font() -> str:
    try:
        fams = set(QFontDatabase.families())
    except Exception:
        fams = set()
    for f in (
        "SF Pro Display",
        "PingFang SC",
        "HarmonyOS Sans SC",
        "Microsoft YaHei UI",
        "Microsoft YaHei",
        "Segoe UI",
    ):
        if f in fams:
            return f
    return "Microsoft YaHei UI"


# ══════════════════════════════════════════════════════════════════════
# 玻璃背景
# ══════════════════════════════════════════════════════════════════════
class GlassRoot(QWidget):
    """带渐变底和光斑的根容器。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        r = self.rect()
        path = QPainterPath()
        path.addRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), 20, 20)
        p.setClipPath(path)

        base = QLinearGradient(0, 0, r.width(), r.height())
        base.setColorAt(0.0, QColor("#EAF1FF"))
        base.setColorAt(0.45, QColor("#F4EEFF"))
        base.setColorAt(1.0, QColor("#E8F7FF"))
        p.fillPath(path, base)

        for cx, cy, rad, col in (
            (0.12, 0.05, 0.55, QColor(120, 170, 255, 90)),
            (0.92, 0.18, 0.5, QColor(190, 150, 255, 80)),
            (0.55, 1.02, 0.6, QColor(120, 230, 220, 70)),
        ):
            g = QRadialGradient(r.width() * cx, r.height() * cy, r.width() * rad)
            g.setColorAt(0.0, col)
            g.setColorAt(1.0, QColor(col.red(), col.green(), col.blue(), 0))
            p.fillRect(r, g)

        p.setPen(QPen(QColor(255, 255, 255, 210), 1.2))
        p.drawPath(path)


class HashChart(QWidget):
    """算力走势图：滚动窗口，纯 QPainter 绘制。"""

    def __init__(self, minutes: int = 15, parent=None):
        super().__init__(parent)
        self.minutes = minutes
        self.data: deque = deque(maxlen=2400)
        self.setMinimumHeight(120)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def add_sample(self, th: float):
        self.data.append((time.time(), float(th)))
        self.update()

    def clear(self):
        self.data.clear()
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        pl, pr, pt, pb = 8, 8, 20, 16
        plot = QRectF(pl, pt, w - pl - pr, h - pt - pb)

        now = time.time()
        if self.data:
            first_t = self.data[0][0]
            span = min(self.minutes * 60.0, max(now - first_t, 60.0))
        else:
            span = 60.0
        t0 = now - span
        pts = [(t, v) for t, v in self.data if t >= t0]

        # 背景网格
        p.setPen(QPen(QColor(120, 130, 160, 40), 1, Qt.PenStyle.DashLine))
        for i in range(1, 4):
            y = plot.top() + plot.height() * i / 4
            p.drawLine(int(plot.left()), int(y), int(plot.right()), int(y))

        if len(pts) < 2:
            p.setPen(QColor(140, 145, 160))
            f = QFont(self.font())
            f.setPointSize(9)
            p.setFont(f)
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter, "等待算力数据…")
            return

        vmax = max(v for _, v in pts)
        vmin = min(v for _, v in pts)
        top = max(vmax * 1.12, vmax + 1.0)
        bottom = max(0.0, min(vmin * 0.85, vmax * 0.5))

        def xmap(t: float) -> float:
            return plot.left() + plot.width() * (t - t0) / max(span, 1.0)

        def ymap(v: float) -> float:
            span = max(top - bottom, 1e-6)
            return plot.bottom() - plot.height() * (v - bottom) / span

        line = QPainterPath()
        for i, (t, v) in enumerate(pts):
            x, y = xmap(t), ymap(v)
            if i == 0:
                line.moveTo(x, y)
            else:
                line.lineTo(x, y)

        area = QPainterPath(line)
        area.lineTo(xmap(pts[-1][0]), plot.bottom())
        area.lineTo(xmap(pts[0][0]), plot.bottom())
        area.closeSubpath()

        grad = QLinearGradient(0, plot.top(), 0, plot.bottom())
        grad.setColorAt(0.0, QColor(10, 132, 255, 120))
        grad.setColorAt(1.0, QColor(10, 132, 255, 8))
        p.fillPath(area, grad)

        pen = QPen(QColor(10, 132, 255, 235), 2.0)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawPath(line)

        # 当前值光点
        cx, cy = xmap(pts[-1][0]), ymap(pts[-1][1])
        p.setBrush(QColor(255, 255, 255, 235))
        p.setPen(QPen(QColor(10, 132, 255), 2))
        p.drawEllipse(QRectF(cx - 3.5, cy - 3.5, 7, 7))

        # 文本
        f = QFont(self.font())
        f.setPointSize(8)
        p.setFont(f)
        p.setPen(QColor(125, 130, 148))
        p.drawText(
            QRectF(plot.left(), 2, plot.width() / 2, 14),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"最近 {max(1, int(round(span / 60)))} 分钟 · 每 2 秒采样",
        )
        p.drawText(
            QRectF(plot.left() + plot.width() / 2, 2, plot.width() / 2, 14),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"最高 {vmax:.2f} · 最低 {vmin:.2f} TH/s",
        )
        p.setPen(QColor(150, 155, 170))
        p.drawText(
            QRectF(plot.left(), plot.bottom() + 2, plot.width(), 14),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"-{max(1, int(round(span / 60)))} 分钟",
        )
        p.drawText(
            QRectF(plot.left(), plot.bottom() + 2, plot.width(), 14),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            "现在",
        )


def scan_buckets(log_dir: Path, mode: str = "hour") -> list[dict]:
    """按 小时/天/月 统计日志里的份额，返回从早到晚的桶。"""
    now = datetime.now()

    if mode == "day":
        count = 14
        base = now.replace(hour=0, minute=0, second=0, microsecond=0)
        keys = [base - timedelta(days=i) for i in range(count - 1, -1, -1)]
        labels = [f"{k:%m-%d}" for k in keys]
        trunc = lambda d: d.replace(hour=0, minute=0, second=0, microsecond=0)  # noqa: E731
        title = f"每天份额 · 近 {count} 天"
    elif mode == "month":
        count = 12
        keys = []
        for i in range(count - 1, -1, -1):
            mm = now.month - i
            yy = now.year
            while mm <= 0:
                mm += 12
                yy -= 1
            keys.append(datetime(yy, mm, 1))
        labels = [f"{k:%m}月" for k in keys]
        trunc = lambda d: d.replace(day=1, hour=0, minute=0, second=0, microsecond=0)  # noqa: E731
        title = f"每月份额 · 近 {count} 个月"
    else:
        count = 12
        base = now.replace(minute=0, second=0, microsecond=0)
        keys = [base - timedelta(hours=i) for i in range(count - 1, -1, -1)]
        labels = [f"{k:%H}时" for k in keys]
        trunc = lambda d: d.replace(minute=0, second=0, microsecond=0)  # noqa: E731
        title = f"每小时份额 · 近 {count} 小时"

    buckets: dict[datetime, dict] = {}
    for k, lb in zip(keys, labels):
        buckets[k] = {
            "key": k,
            "label": lb,
            "accepted": 0,
            "rejected": 0,
            "stale": 0,
            "title": title,
        }

    if log_dir and log_dir.exists():
        for f in sorted(log_dir.glob("srb-*.log")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for line in text.splitlines():
                m = re.match(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\]", line)
                if not m:
                    continue
                try:
                    ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                b = buckets.get(trunc(ts))
                if b is None:
                    continue
                if "share accepted" in line:
                    b["accepted"] += 1
                elif "share rejected" in line:
                    b["rejected"] += 1
                    if "stale" in line:
                        b["stale"] += 1

    return [buckets[k] for k in keys]


class HourlyShareChart(QWidget):
    """按小时统计份额的柱状图。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.buckets: list[dict] = []
        self.title = "份额统计"
        self.setMinimumHeight(110)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def set_data(self, buckets: list[dict]):
        self.buckets = buckets
        if buckets and buckets[0].get("title"):
            self.title = buckets[0]["title"]
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        pl, pr, pt, pb = 8, 8, 20, 18
        plot = QRectF(pl, pt, w - pl - pr, h - pt - pb)

        if not self.buckets:
            p.setPen(QColor(140, 145, 160))
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter, "等待份额数据…")
            return

        vmax = max((b["accepted"] + b["rejected"]) for b in self.buckets) or 1
        vmax = vmax * 1.15
        n = len(self.buckets)
        slot = plot.width() / n
        bar_w = max(6.0, slot * 0.56)

        p.setPen(QPen(QColor(120, 130, 160, 35), 1))
        p.drawLine(int(plot.left()), int(plot.bottom()), int(plot.right()), int(plot.bottom()))

        f = QFont(self.font())
        f.setPointSize(7)
        p.setFont(f)

        for i, b in enumerate(self.buckets):
            cx = plot.left() + slot * (i + 0.5)
            acc = b["accepted"]
            rej = b["rejected"]

            total_h = plot.height() * (acc + rej) / vmax if vmax else 0
            acc_h = plot.height() * acc / vmax if vmax else 0
            rej_h = total_h - acc_h
            y_bottom = plot.bottom()

            if acc_h > 0:
                r = QRectF(cx - bar_w / 2, y_bottom - acc_h, bar_w, acc_h)
                path = QPainterPath()
                path.addRoundedRect(r, 3, 3)
                g = QLinearGradient(0, r.top(), 0, r.bottom())
                g.setColorAt(0.0, QColor(10, 132, 255, 235))
                g.setColorAt(1.0, QColor(10, 132, 255, 120))
                p.fillPath(path, g)
            if rej_h > 0:
                r = QRectF(cx - bar_w / 2, y_bottom - total_h, bar_w, rej_h)
                path = QPainterPath()
                path.addRoundedRect(r, 3, 3)
                p.fillPath(path, QColor(255, 120, 110, 210))

            # 数值
            if acc + rej > 0:
                p.setPen(QColor(110, 115, 132))
                p.drawText(
                    QRectF(cx - slot / 2, y_bottom - total_h - 13, slot, 12),
                    Qt.AlignmentFlag.AlignCenter,
                    str(acc + rej),
                )

            # 时间标签
            p.setPen(QColor(150, 155, 170))
            if n <= 14 or i % 2 == 0:
                p.drawText(
                    QRectF(cx - slot / 2, y_bottom + 1, slot, 13),
                    Qt.AlignmentFlag.AlignCenter,
                    b["label"],
                )

        p.setPen(QColor(125, 130, 148))
        f2 = QFont(self.font())
        f2.setPointSize(8)
        p.setFont(f2)
        total_acc = sum(b["accepted"] for b in self.buckets)
        total_rej = sum(b["rejected"] for b in self.buckets)
        p.drawText(
            QRectF(plot.left(), 2, plot.width(), 14),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"{self.title}",
        )
        p.drawText(
            QRectF(plot.left(), 2, plot.width(), 14),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"蓝=接受 {total_acc} · 红=失败 {total_rej}",
        )


class GlassSegments(QWidget):
    """浅色玻璃分段控件。"""

    def __init__(self, items: list[str], on_change, parent=None):
        super().__init__(parent)
        self._on_change = on_change
        self._idx = 0
        self.btns: list[QPushButton] = []

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(2)

        for i, text in enumerate(items):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setChecked(i == 0)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(22)
            b.setStyleSheet(
                """
                QPushButton {
                    border: none; border-radius: 11px; padding: 0 12px;
                    font-size: 11px; color: #5A5A66; background: transparent;
                }
                QPushButton:hover { background: rgba(255,255,255,0.55); }
                QPushButton:checked {
                    background: rgba(255,255,255,0.95);
                    color: #1D1D1F; font-weight: 600;
                }
                """
            )
            b.clicked.connect(lambda _=False, idx=i: self._select(idx))
            h.addWidget(b)
            self.btns.append(b)

    def _select(self, idx: int):
        self._idx = idx
        for i, b in enumerate(self.btns):
            b.setChecked(i == idx)
        self._on_change(idx)


class GlassCard(QFrame):

    def __init__(self, radius: int = 16, alpha: int = 150, parent=None):
        super().__init__(parent)
        self._radius = radius
        self._alpha = alpha
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)

        path = QPainterPath()
        path.addRoundedRect(r, self._radius, self._radius)

        p.fillPath(path, QColor(255, 255, 255, self._alpha))
        p.setPen(QPen(QColor(255, 255, 255, 235), 1.1))
        p.drawPath(path)


# ══════════════════════════════════════════════════════════════════════
# 窗口按钮
# ══════════════════════════════════════════════════════════════════════
class GlassButton(QAbstractButton):
    """右上角窗口按钮：最小化 / 最大化 / 关闭。"""

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = False
        self.setToolTip({"min": "最小化", "max": "最大化", "close": "关闭"}[kind])

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)

        bg = QColor(255, 255, 255, 90)
        fg = QColor("#4A4A55")
        if self._hover:
            if self.kind == "close":
                bg, fg = QColor(255, 95, 87, 235), QColor(255, 255, 255)
            else:
                bg, fg = QColor(255, 255, 255, 210), QColor("#1D1D1F")

        p.setBrush(bg)
        p.setPen(QPen(QColor(255, 255, 255, 180), 1.0))
        p.drawEllipse(r)

        p.setPen(QPen(fg, 1.5))
        cx, cy = r.center().x(), r.center().y()
        if self.kind == "min":
            p.drawLine(int(cx - 4), int(cy + 3), int(cx + 4), int(cy + 3))
        elif self.kind == "max":
            p.drawRect(QRectF(cx - 4, cy - 4, 8, 8))
        else:
            p.drawLine(int(cx - 4), int(cy - 4), int(cx + 4), int(cy + 4))
            p.drawLine(int(cx + 4), int(cy - 4), int(cx - 4), int(cy + 4))


class PillSwitch(QAbstractButton):
    """启动/停止胶囊按钮。"""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(40)
        self.setMinimumWidth(112)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        on = self.isChecked()

        path = QPainterPath()
        path.addRoundedRect(r, r.height() / 2, r.height() / 2)

        if on:
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            g.setColorAt(0.0, QColor("#FF6B6B"))
            g.setColorAt(1.0, QColor("#E5484D"))
            p.fillPath(path, g)
            p.setPen(QPen(QColor(255, 255, 255, 90), 1.0))
            p.drawPath(path)
            p.setPen(QColor(255, 255, 255))
        else:
            p.fillPath(path, QColor(255, 255, 255, 165))
            p.setPen(QPen(QColor(255, 255, 255, 230), 1.0))
            p.drawPath(path)
            p.setPen(QColor("#2C2C33"))

        f = QFont(self.font())
        f.setPointSize(9)
        f.setBold(True)
        p.setFont(f)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())


# ══════════════════════════════════════════════════════════════════════
# API 轮询线程
# ══════════════════════════════════════════════════════════════════════
class ApiPoller(QThread):
    got = pyqtSignal(dict)

    def __init__(self, port: int, interval: float = 2.0, parent=None):
        super().__init__(parent)
        self.port = port
        self.interval = interval
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        url = f"http://127.0.0.1:{self.port}/"
        while not self._stop.is_set():
            try:
                req = urllib.request.Request(url, headers={"Cache-Control": "no-store"})
                with urllib.request.urlopen(req, timeout=2) as resp:
                    data = json.loads(resp.read().decode("utf-8", "replace"))
                self.got.emit({"ok": True, "data": data})
            except Exception as e:  # noqa: BLE001
                self.got.emit({"ok": False, "error": str(e)[:80]})
            self._stop.wait(self.interval)


# ══════════════════════════════════════════════════════════════════════
# 设置对话框
# ══════════════════════════════════════════════════════════════════════
class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.cfg = dict(cfg)
        self.setWindowTitle("设置")
        self.setModal(True)
        available = self.screen().availableGeometry()
        self.resize(
            min(660, max(520, available.width() - 48)),
            min(760, max(360, available.height() - 70)),
        )
        self.setStyleSheet(
            """
            QDialog { background: #F7F8FC; }
            QLabel { color:#3A3A44; font-size:12px; }
            QLineEdit, QSpinBox {
                background:#FFFFFF; border:1px solid #DCDCE4; border-radius:8px;
                padding:6px 10px; font-size:12px; color:#1D1D1F;
            }
            QPushButton {
                background:#FFFFFF; border:1px solid #DCDCE4; border-radius:8px;
                padding:7px 16px; font-size:12px; color:#1D1D1F;
            }
            QPushButton:hover { background:#F0F2F8; }
            QPushButton#primary {
                background:#0A84FF; border:none; color:white; font-weight:600;
            }
            QPushButton#primary:hover { background:#0070E0; }
            """
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background:#F7F8FC; }")
        body = QWidget()
        scroll.setWidget(body)
        outer.addWidget(scroll)
        v = QVBoxLayout(body)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(12)

        def row(label: str, widget: QWidget, extra: QWidget | None = None):
            h = QHBoxLayout()
            h.setSpacing(8)
            lb = QLabel(label)
            lb.setFixedWidth(92)
            h.addWidget(lb)
            h.addWidget(widget, 1)
            if extra:
                h.addWidget(extra)
            v.addLayout(h)

        self.ed_exe = QLineEdit(str(resolve_path(self.cfg.get("miner_exe", ""))))
        btn_browse = QPushButton("浏览…")
        btn_browse.setFixedWidth(72)
        btn_browse.clicked.connect(self._browse)
        row("矿工程序", self.ed_exe, btn_browse)

        self.ed_pool = QLineEdit(self.cfg.get("pool", ""))
        row("矿池", self.ed_pool)

        self.ed_account = QLineEdit(self.cfg.get("account", ""))
        row("收款账户", self.ed_account)

        self.ed_worker = QLineEdit(_normalize_worker(self.cfg.get("worker", "")))
        self.ed_worker.setPlaceholderText("矿池里显示的矿工名，例如 RTX3060")
        self.gpu_label = QLabel("正在检测显卡…")
        self.gpu_label.setWordWrap(True)
        self.gpu_label.setStyleSheet("color:#6E6E7A; font-size:11px;")
        row("矿工名称", self.ed_worker, self.gpu_label)

        self.sp_port = QSpinBox()
        self.sp_port.setRange(1024, 65535)
        self.sp_port.setValue(int(self.cfg.get("api_port", 21550)))
        self.sp_port.setFixedWidth(110)
        row("API 端口", self.sp_port)

        self.sp_fan = QSpinBox()
        self.sp_fan.setRange(0, 100)
        self.sp_fan.setValue(int(self.cfg.get("fan", 0)))
        self.sp_fan.setSuffix(" %")
        self.sp_fan.setSpecialValueText("不设置")
        self.sp_fan.setFixedWidth(110)
        row("风扇转速", self.sp_fan)

        self.sp_power = QSpinBox()
        self.power_range = None
        saved_power = int(self.cfg.get("power_limit", 0))
        self.sp_power.setRange(0, max(1000, saved_power))
        self.sp_power.setValue(saved_power)
        self.sp_power.setSuffix(" W")
        self.sp_power.setSpecialValueText("保持现状")
        self.sp_power.setFixedWidth(150)
        power_tip = "0 表示不下发功耗设置。非零值为瓦数上限，须在显卡驱动允许范围内。"
        self.sp_power.setToolTip(power_tip)
        row("功耗上限", self.sp_power)

        self.sp_core = QSpinBox()
        self.sp_core.setRange(-1000, 1000)
        self.sp_core.setValue(int(self.cfg.get("core_offset", 0)))
        self.sp_core.setSuffix(" MHz")
        self.sp_core.setSpecialValueText("不设置")
        self.sp_core.setFixedWidth(110)
        row("核心偏移", self.sp_core)

        self.sp_mem = QSpinBox()
        self.sp_mem.setRange(-3000, 3000)
        self.sp_mem.setValue(int(self.cfg.get("mem_offset", 0)))
        self.sp_mem.setSuffix(" MHz")
        self.sp_mem.setSpecialValueText("不设置")
        self.sp_mem.setFixedWidth(110)
        row("显存偏移", self.sp_mem)

        self.ed_extra = QLineEdit(self.cfg.get("extra_args", ""))
        row("附加参数", self.ed_extra)

        self.ck_sched = QCheckBox("启用定时启停")
        self.ck_sched.setChecked(bool(self.cfg.get("sched_enable", False)))
        self.ck_sched.setStyleSheet("QCheckBox { font-size:12px; color:#3A3A44; }")
        row("定时启停", self.ck_sched)

        self.tm_start = QTimeEdit()
        self.tm_start.setDisplayFormat("HH:mm")
        self.tm_start.setTime(QTime.fromString(str(self.cfg.get("sched_start", "07:00")), "HH:mm"))
        self.tm_start.setFixedWidth(110)
        row("每天启动", self.tm_start)

        self.tm_stop = QTimeEdit()
        self.tm_stop.setDisplayFormat("HH:mm")
        self.tm_stop.setTime(QTime.fromString(str(self.cfg.get("sched_stop", "23:00")), "HH:mm"))
        self.tm_stop.setFixedWidth(110)
        row("每天停止", self.tm_stop)

        self.ck_hotkey = QCheckBox("启用 Alt+0 全局呼出 / 隐藏到托盘")
        self.ck_hotkey.setChecked(bool(self.cfg.get("global_hotkey", True)))
        self.ck_hotkey.setStyleSheet("QCheckBox { font-size:12px; color:#3A3A44; }")
        row("全局快捷键", self.ck_hotkey)

        self.ck_tray = QCheckBox("点击关闭按钮时隐藏到托盘，继续 PRL 计算")
        self.ck_tray.setChecked(bool(self.cfg.get("close_to_tray", True)))
        self.ck_tray.setStyleSheet("QCheckBox { font-size:12px; color:#3A3A44; }")
        row("关闭行为", self.ck_tray)

        self.lb_miner_versions = QLabel()
        row("矿工更新", self.lb_miner_versions)
        self.ck_miner_update = QCheckBox("自动更新 SRBMiner")
        self.ck_miner_update.setChecked(self.cfg.get("miner_auto_update") is True)
        row("", self.ck_miner_update)
        self.lb_update_status = QLabel()
        self.lb_update_status.setWordWrap(True)
        row("", self.lb_update_status)

        actions = QWidget()
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(0, 0, 0, 0)
        self.btn_check_miner = QPushButton("立即检查更新")
        self.btn_update_miner = QPushButton("立即更新")
        action_layout.addWidget(self.btn_check_miner)
        action_layout.addWidget(self.btn_update_miner)
        action_layout.addStretch(1)
        row("", actions)
        if parent is not None:
            self.btn_check_miner.clicked.connect(parent._check_miner_update)
            self.btn_update_miner.clicked.connect(parent._download_miner_update)
            parent.miner_update_changed.connect(self._refresh_miner_update)
            self._refresh_miner_update()

        tip = QLabel(
            "矿池填 host:port（不写 stratum://）。矿工名称会拼成 账户/矿工名 作为 wallet，\n"
            "这是 Kryptex 认的格式，面板里显示的就是这个名称。\n"
            "功耗 0 = 保持现状；能否限制瓦数取决于显卡驱动，笔记本可能不支持。\n"
            "功耗/风扇/偏移等算力引擎参数保存后，点击主界面「立即重启矿工」生效。\n"
            "快捷键和关闭行为保存后立即生效；Alt+0 使用主键盘数字 0。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#8A8A95; font-size:11px;")
        v.addWidget(tip)

        h = QHBoxLayout()
        h.addStretch(1)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("保存")
        btn_ok.setObjectName("primary")
        btn_ok.clicked.connect(self._ok)
        h.addWidget(btn_cancel)
        h.addWidget(btn_ok)
        h.setContentsMargins(20, 8, 20, 16)
        outer.addLayout(h)
        self._hardware_results = SimpleQueue()
        self._hardware_timer = QTimer(self)
        self._hardware_timer.setInterval(80)
        self._hardware_timer.timeout.connect(self._poll_hardware)
        self.finished.connect(self._hardware_timer.stop)
        self._hardware_timer.start()
        results = self._hardware_results

        def probe():
            try:
                gpu = detect_gpu().get("gpu_name", "")
            except Exception:
                gpu = ""
            results.put(("gpu", gpu))
            try:
                power_range = query_power_range()
            except Exception:
                power_range = None
            results.put(("power", power_range))

        threading.Thread(target=probe, name="Settings hardware detection", daemon=True).start()

    def _poll_hardware(self):
        while True:
            try:
                kind, value = self._hardware_results.get_nowait()
            except Empty:
                return
            if kind == "gpu":
                self.gpu_label.setText(value or "未识别独立显卡")
                self.ed_worker.setToolTip(f"本机显卡：{value or '未识别'}")
                continue
            self._hardware_timer.stop()
            self.power_range = value
            if value:
                low, high = value
                self.sp_power.setMaximum(max(high, self.sp_power.value()))
                self.sp_power.setToolTip(
                    f"{self.sp_power.toolTip()}本机驱动报告范围：{low}–{high} W。"
                )
            return

    def _refresh_miner_update(self):
        window = self.parent()
        current = window.miner_version or "未知"
        latest = window.miner_release.version if window.miner_release else "未检查"
        self.lb_miner_versions.setText(f"当前 {current}  ·  最新 {latest}")
        self.lb_update_status.setText(window.miner_update_status)
        self.btn_check_miner.setEnabled(not window.miner_update_busy)
        self.btn_update_miner.setEnabled(not window.miner_update_busy and window._miner_update_available())

    def _browse(self):
        start = str(Path(self.ed_exe.text()).parent) if self.ed_exe.text() else ""
        f, _ = QFileDialog.getOpenFileName(
            self, "选择 SRBMiner-MULTI.exe", start, "可执行文件 (*.exe)"
        )
        if f:
            self.ed_exe.setText(f)

    def _ok(self):
        power = self.sp_power.value()
        if power and power != self.cfg.get("power_limit", 0) and self.power_range:
            low, high = self.power_range
            if not low <= power <= high:
                QMessageBox.warning(self, "功耗超出范围", f"请填 0 保持现状，或填写 {low}–{high} W。")
                return
        selected = self.ed_exe.text().strip()
        try:
            self.cfg["miner_exe"] = str(Path(selected).resolve().relative_to(app_dir().resolve()))
        except (OSError, ValueError):
            self.cfg["miner_exe"] = selected
        self.cfg["pool"] = self.ed_pool.text().strip()
        self.cfg["account"] = self.ed_account.text().strip()
        self.cfg["worker"] = _normalize_worker(self.ed_worker.text())
        self.cfg["extra_args"] = self.ed_extra.text().strip()
        self.cfg["api_port"] = int(self.sp_port.value())
        self.cfg["fan"] = int(self.sp_fan.value())
        self.cfg["power_limit"] = int(self.sp_power.value())
        self.cfg["global_hotkey"] = bool(self.ck_hotkey.isChecked())
        self.cfg["close_to_tray"] = bool(self.ck_tray.isChecked())
        self.cfg["miner_auto_update"] = self.ck_miner_update.isChecked()
        self.cfg["core_offset"] = int(self.sp_core.value())
        self.cfg["mem_offset"] = int(self.sp_mem.value())
        self.cfg["sched_enable"] = bool(self.ck_sched.isChecked())
        self.cfg["sched_start"] = self.tm_start.time().toString("HH:mm")
        self.cfg["sched_stop"] = self.tm_stop.time().toString("HH:mm")
        self.accept()


# ══════════════════════════════════════════════════════════════════════
# 主窗口
# ══════════════════════════════════════════════════════════════════════
class MainWindow(QWidget):
    miner_update_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self._quitting = False
        self._hidden_dialogs = []
        self._background_hidden = False
        self._temporary_hotkey = False
        self.miner_install = self._miner_install()
        self.running_install = None
        self.miner_version = binary_version(self.miner_install.executable)
        self.miner_release: Release | None = None
        self.miner_update_busy = False
        self.miner_update_status = "等待检查"
        self._miner_results = SimpleQueue()
        self._trial_write_failed = False

        self.proc: subprocess.Popen | None = None
        self.running = False
        self.started_at: float | None = None
        self.restarts = 0
        self.want_run = False
        self.last_api = 0.0
        self.api_fails = 0
        self.zero_streak = 0
        self.pending_restart_at: float | None = None
        self.grace_until = 0.0
        self.last_mining_time: int | None = None
        self.stale_count = 0
        self.stats: dict = {}
        self.log_path: Path | None = None
        self.session_log: Path | None = None
        self._log_pos = 0
        self.sched_fired: dict[str, object] = {}
        self.pool_wait = False
        self.share_stat = {"accepted": 0, "rejected": 0, "stale": 0, "lat_sum": 0.0, "lat_n": 0}

        self.setWindowTitle(WINDOW_TITLE)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(800, 940)
        self.setMinimumSize(720, 520)

        self._drag_pos = None
        self._build_ui()
        self._center()
        self._setup_tray()
        self.hotkey = GlobalHotkey(self)
        QApplication.instance().installNativeEventFilter(self.hotkey)
        self._apply_hotkey()

        self.poller: ApiPoller | None = None
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

        self.log_timer = QTimer(self)
        self.log_timer.setInterval(2000)
        self.log_timer.timeout.connect(self._refresh_log)
        self.log_timer.start()

        self.hourly_timer = QTimer(self)
        self.hourly_timer.setInterval(30000)
        self.hourly_timer.timeout.connect(self._refresh_hourly)
        self.hourly_timer.start()
        self._refresh_hourly()

        self._set_status("已停止", "#8A8A95")
        self._update_sched_label()
        self._log("守护器就绪")
        found = discover_miner() if not self._exe_ok() else None
        if found:
            try:
                relative = Path(found).resolve().relative_to(app_dir().resolve())
                self.cfg["miner_exe"] = str(relative)
            except (OSError, ValueError):
                self.cfg["miner_exe"] = found
            save_config(self.cfg)
            self.miner_install = self._miner_install()
            self.miner_version = binary_version(self.miner_install.executable)
            self._log(f"已自动找到矿工：{found}")
            self._refresh_hourly()
        elif not self._exe_ok():
            self._log("未找到矿工程序，请点右下角「设置」选择 SRBMiner-MULTI.exe")
            QTimer.singleShot(500, self._open_settings)

        # 打开程序即自动开挖
        QTimer.singleShot(800, self._auto_boot)
        QTimer.singleShot(1000, self._scheduled_miner_check)
        self.update_timer = QTimer(self)
        self.update_timer.setInterval(CHECK_INTERVAL_MS)
        self.update_timer.timeout.connect(self._scheduled_miner_check)
        self.update_timer.start()

    # ── 布局 ────────────────────────────────────────────────────────
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.root = GlassRoot()
        outer.addWidget(self.root)

        v = QVBoxLayout(self.root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        v.addWidget(self._build_titlebar())

        self.main_scroll = QScrollArea()
        self.main_scroll.setWidgetResizable(True)
        self.main_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.main_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.main_scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background:transparent; border:none; }"
        )
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(22, 4, 22, 20)
        bl.setSpacing(14)
        self.main_scroll.setWidget(body)
        v.addWidget(self.main_scroll, 1)

        # 状态卡
        card = GlassCard(radius=18, alpha=160)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(20, 16, 20, 16)
        cv.setSpacing(6)

        top = QHBoxLayout()
        self.lb_dot = QLabel("●")
        self.lb_dot.setStyleSheet("font-size:15px; color:#8A8A95;")
        self.lb_status = QLabel("已停止")
        self.lb_status.setStyleSheet(
            "font-size:15px; font-weight:600; color:#1D1D1F;"
        )
        self.lb_uptime = QLabel("—")
        self.lb_uptime.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.lb_uptime.setStyleSheet("font-size:12px; color:#6E6E7A;")
        top.addWidget(self.lb_dot)
        top.addWidget(self.lb_status)
        top.addStretch(1)
        top.addWidget(self.lb_uptime)
        cv.addLayout(top)

        self.lb_hash = QLabel("—")
        self.lb_hash.setStyleSheet(
            "font-size:44px; font-weight:700; color:#12121A; letter-spacing:-1px;"
        )
        cv.addWidget(self.lb_hash)

        self.lb_hash_sub = QLabel("1 分钟 —   ·   15 分钟 —")
        self.lb_hash_sub.setStyleSheet("font-size:12px; color:#6E6E7A;")
        cv.addWidget(self.lb_hash_sub)
        bl.addWidget(card)

        # 算力走势
        chart_card = GlassCard(radius=16, alpha=135)
        chv = QVBoxLayout(chart_card)
        chv.setContentsMargins(12, 8, 12, 6)
        self.chart = HashChart(minutes=15)
        chv.addWidget(self.chart, 1)
        chart_card.setFixedHeight(168)
        bl.addWidget(chart_card)

        # 每小时份额
        hourly_card = GlassCard(radius=16, alpha=135)
        hv = QVBoxLayout(hourly_card)
        hv.setContentsMargins(12, 8, 12, 6)
        hv.setSpacing(4)

        hh = QHBoxLayout()
        hh.setContentsMargins(0, 0, 0, 0)
        self.hourly_mode = "hour"
        self.seg_period = GlassSegments(["小时", "天", "月"], self._on_period_changed)
        hh.addStretch(1)
        hh.addWidget(self.seg_period)
        hv.addLayout(hh)

        self.hourly = HourlyShareChart()
        hv.addWidget(self.hourly, 1)
        hourly_card.setFixedHeight(196)
        bl.addWidget(hourly_card)

        # 指标四宫格
        grid_card = GlassCard(radius=16, alpha=140)
        g = QGridLayout(grid_card)
        g.setContentsMargins(18, 14, 18, 14)
        g.setHorizontalSpacing(18)
        g.setVerticalSpacing(12)

        self.metrics: dict[str, QLabel] = {}
        items = [
            ("accepted", "挖到（接受）"),
            ("rejected", "提交失败"),
            ("stale", "其中延迟"),
            ("temp", "温度"),
            ("power", "功耗"),
            ("latency", "平均提交延迟"),
        ]
        for i, (key, title) in enumerate(items):
            box = QVBoxLayout()
            box.setSpacing(2)
            t = QLabel(title)
            t.setStyleSheet("font-size:11px; color:#7C7C88;")
            val = QLabel("—")
            val.setStyleSheet("font-size:17px; font-weight:600; color:#1D1D1F;")
            box.addWidget(t)
            box.addWidget(val)
            self.metrics[key] = val
            g.addLayout(box, i // 3, i % 3)
        bl.addWidget(grid_card)

        # 日志
        log_card = GlassCard(radius=16, alpha=125)
        lv = QVBoxLayout(log_card)
        lv.setContentsMargins(14, 12, 14, 12)
        lv.setSpacing(6)

        head = QHBoxLayout()
        lt = QLabel("运行日志")
        lt.setStyleSheet("font-size:11px; font-weight:600; color:#7C7C88;")
        self.lb_pool = QLabel("—")
        self.lb_pool.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.lb_pool.setStyleSheet("font-size:11px; color:#7C7C88;")
        head.addWidget(lt)
        head.addStretch(1)
        head.addWidget(self.lb_pool)
        lv.addLayout(head)

        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMinimumHeight(120)
        self.txt_log.setStyleSheet(
            """
            QPlainTextEdit {
                background: rgba(255,255,255,0.55);
                border: 1px solid rgba(255,255,255,0.75);
                border-radius: 10px;
                padding: 8px;
                color: #3C3C46;
                font-family: Consolas, "Cascadia Mono", monospace;
                font-size: 11px;
            }
            """
        )
        lv.addWidget(self.txt_log, 1)
        bl.addWidget(log_card, 1)

        # 底部按钮
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.btn_run = PillSwitch("启动守护")
        self.btn_run.clicked.connect(self._on_toggle)
        bar.addWidget(self.btn_run)

        self.btn_restart = QPushButton("立即重启矿工")
        self.btn_restart.setFixedHeight(40)
        self.btn_restart.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_restart.clicked.connect(self._manual_restart)
        bar.addWidget(self.btn_restart)

        bar.addStretch(1)

        self.lb_sched = QLabel("")
        self.lb_sched.setStyleSheet("font-size:11px; color:#6E6E7A;")
        bar.addWidget(self.lb_sched)

        self.btn_settings = QPushButton("设置")
        self.btn_settings.setFixedHeight(40)
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings.clicked.connect(self._open_settings)
        bar.addWidget(self.btn_settings)

        self.btn_background = QPushButton("后台隐藏")
        self.btn_background.setFixedHeight(40)
        self.btn_background.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_background.setToolTip("隐藏窗口和托盘图标，按 Alt+0 恢复")
        self.btn_background.clicked.connect(self._hide_in_background)
        bar.insertWidget(bar.count() - 1, self.btn_background)

        for b in (self.btn_restart, self.btn_settings, self.btn_background):
            b.setStyleSheet(
                """
                QPushButton {
                    background: rgba(255,255,255,0.72);
                    border: 1px solid rgba(255,255,255,0.85);
                    border-radius: 20px;
                    padding: 0 20px;
                    font-size: 12.5px;
                    color: #2C2C33;
                }
                QPushButton:hover { background: rgba(255,255,255,0.92); }
                QPushButton:pressed { background: rgba(235,238,248,0.95); }
                """
            )
        bl.addLayout(bar)
        self._fit_to_screen()

    def _fit_to_screen(self):
        screen = self.screen() or QApplication.primaryScreen()
        if not screen:
            return
        available = screen.availableGeometry()
        width = min(800, max(720, available.width() - 24))
        height = min(940, max(520, available.height() - 24))
        self.resize(width, height)
        if hasattr(self, "main_scroll"):
            # 矮屏改用滚动，不把完整仪表盘压到不可读。
            self.main_scroll.widget().setMinimumHeight(860 if height < 900 else 0)

    def _build_titlebar(self) -> QWidget:
        tb = QWidget()
        tb.setFixedHeight(52)
        tb.mousePressEvent = self._tb_press
        tb.mouseMoveEvent = self._tb_move
        tb.mouseReleaseEvent = self._tb_release

        h = QHBoxLayout(tb)
        h.setContentsMargins(22, 8, 14, 0)
        h.setSpacing(8)

        title = QLabel(f"{APP_NAME} V2")
        title.setStyleSheet(
            "font-size:13px; font-weight:600; color:#3A3A44; background:transparent;"
        )
        sub = QLabel("SRBMiner 看门狗")
        sub.setStyleSheet("font-size:11px; color:#8A8A95; background:transparent;")
        h.addWidget(title)
        h.addWidget(sub)
        h.addStretch(1)

        b_min = GlassButton("min")
        b_max = GlassButton("max")
        b_close = GlassButton("close")
        b_min.clicked.connect(self.showMinimized)
        b_max.clicked.connect(self._toggle_max)
        b_close.clicked.connect(self.close)
        for b in (b_min, b_max, b_close):
            h.addWidget(b)
        return tb

    # ── 窗口拖动 ────────────────────────────────────────────────────
    def _tb_press(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _tb_move(self, e):
        if self._drag_pos and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def _tb_release(self, _e):
        self._drag_pos = None

    def _toggle_max(self):
        if self.isMaximized():
            self.showNormal()
            self._fit_to_screen()
        else:
            self.showMaximized()

    def _center(self):
        geo = self.frameGeometry()
        scr = self.screen().availableGeometry().center()
        geo.moveCenter(scr)
        self.move(geo.topLeft())

    # ── 配置与命令 ──────────────────────────────────────────────────
    def _exe_ok(self) -> bool:
        return self.miner_install.executable.is_file()

    def _wallet(self) -> str:
        """账户/矿工名，Kryptex 用斜杠解析矿工名。"""
        acc = str(self.cfg.get("account", "")).strip()
        wk = str(self.cfg.get("worker", "")).strip()
        if "/" in acc:  # 兼容用户直接把 账户/矿工名 填进账户
            base, _, tail = acc.partition("/")
            return f"{base}/{(wk or tail).strip()}"
        return f"{acc}/{wk}" if wk else acc

    def _build_cmd(self) -> list[str]:
        cfg = self.cfg
        args = [
            str(self.miner_install.executable),
            "--algorithm-gpu",
            "pearlhash",
            "--pool",
            cfg["pool"],
            "--wallet",
            self._wallet(),
            "--tls",
            "true",
            "--disable-cpu",
            "--api-enable",
            "--api-port",
            str(cfg["api_port"]),
        ]

        fan = int(cfg.get("fan", 0) or 0)
        power = int(cfg.get("power_limit", 0) or 0)
        core = int(cfg.get("core_offset", 0) or 0)
        mem = int(cfg.get("mem_offset", 0) or 0)

        if fan > 0:
            args.extend(["--gpu-fan0", str(fan)])

        if core == 0 and mem == 0 and power == 0:
            # 没有自定义偏移时，先复位到默认，避免上次残留
            args.append("--gpu-reset-oc")
        else:
            if core:
                args.extend(["--gpu-coffset", str(core)])
            if mem:
                args.extend(["--gpu-moffset", str(mem)])

        if power > 0:
            args.extend(["--gpu-plimit0", str(power)])

        extra = str(cfg.get("extra_args", "")).strip()
        if extra:
            args.extend(extra.split())

        if getattr(self, "session_log", None):
            args.extend(["--log-file", str(self.session_log)])
        return args

    def _open_settings(self):
        dlg = SettingsDialog(self.cfg, self)
        accepted = dlg.exec()
        dlg.deleteLater()
        if accepted:
            try:
                save_config(dlg.cfg)
            except OSError as exc:
                QMessageBox.warning(self, "设置未保存", str(exc))
                return
            auto_was_enabled = self.cfg.get("miner_auto_update") is True
            self.cfg = dlg.cfg
            self._log("设置已保存")
            base = resolve_path(self.cfg["miner_exe"]).resolve()
            changed_miner = base != self.miner_install.base
            if changed_miner:
                self.miner_install = self._miner_install()
                self.miner_version = binary_version(self.miner_install.executable)
                self.miner_release = None
                self._set_update_status("矿工路径已修改，等待检查")
            self._apply_cfg()
            self._apply_hotkey()
            if changed_miner or (self.cfg.get("miner_auto_update") is True and not auto_was_enabled):
                self._scheduled_miner_check()
            if self.want_run:
                self._log("算力引擎参数在下次启动/重启矿工时生效；快捷键与托盘选项已立即生效")

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(QApplication.instance().windowIcon(), self)
        self.tray.setToolTip(WINDOW_TITLE)
        menu = QMenu(self)
        show_action = QAction("显示窗口", self)
        show_action.triggered.connect(self._show_from_tray)
        hide_action = QAction("隐藏到托盘", self)
        hide_action.triggered.connect(self._hide_to_tray)
        settings_action = QAction("设置", self)
        settings_action.triggered.connect(self._tray_settings)
        quit_action = QAction("退出并停止 Pearl 算力引擎", self)
        quit_action.triggered.connect(self._quit_app)
        menu.addAction(show_action)
        menu.addAction(hide_action)
        menu.addAction(settings_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _apply_hotkey(self):
        enabled = bool(self.cfg.get("global_hotkey", True))
        if not self.hotkey.set_enabled(enabled):
            self._log("Alt+0 注册失败，可能已被其他程序占用；仍可用托盘图标显示窗口")
            self.tray.showMessage(APP_NAME, "Alt+0 已被其他程序占用，请从托盘显示窗口。",
                                  QSystemTrayIcon.MessageIcon.Warning, 5000)
        else:
            self._log("Alt+0 全局快捷键已启用" if enabled else "Alt+0 全局快捷键已关闭")

    def _hide_to_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.showMinimized()
            return
        self._hidden_dialogs = [d for d in self.findChildren(QDialog) if d.isVisible()]
        for dialog in self._hidden_dialogs:
            dialog.hide()
        self.hide()

    def _hide_in_background(self):
        if not self.hotkey.set_enabled(True):
            QMessageBox.warning(self, "无法后台隐藏", "Alt+0 注册失败，请关闭占用该快捷键的程序后重试。")
            return
        self._temporary_hotkey = not self.cfg.get("global_hotkey", True)
        self._background_hidden = True
        self.hide()
        self.tray.hide()

    def _show_from_tray(self):
        if self._background_hidden:
            self._background_hidden = False
            self.tray.show()
            if self._temporary_hotkey:
                self._temporary_hotkey = False
                self.hotkey.set_enabled(False)
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()
        dialogs, self._hidden_dialogs = self._hidden_dialogs, []
        for dialog in dialogs:
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()

    def _toggle_tray(self):
        if not self._background_hidden and self.isVisible() and not self.isMinimized():
            self._hide_to_tray()
        else:
            self._show_from_tray()

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._show_from_tray()

    def _tray_settings(self):
        self._show_from_tray()
        if not self._hidden_dialogs and QApplication.activeModalWidget() is None:
            self._open_settings()

    def _quit_app(self):
        self._quitting = True
        self.close()

    def _apply_cfg(self):
        exe = self.cfg.get("miner_exe", "")
        self._log_path_from_exe(exe)
        if self.poller:
            self.poller.stop()
            self.poller.wait(1500)
            self.poller = None
        self._start_poller()
        self._update_sched_label()
        if not self._exe_ok():
            self._log("矿工程序路径无效，请重新设置")

    def _log_path_from_exe(self, exe: str):
        if not exe:
            self.log_path = None
            return
        d = resolve_path(exe).parent
        self.log_path = d / "srb-supervisor.log"

    def _miner_install(self) -> MinerInstall:
        return MinerInstall(app_dir() / "miner-updates", resolve_path(self.cfg["miner_exe"]))

    def _set_update_status(self, status: str):
        self.miner_update_status = status
        self.miner_update_changed.emit()

    def _miner_update_available(self) -> bool:
        release = self.miner_release
        return bool(
            self.miner_version and release and release.sha256
            and version_key(release.version) > version_key(self.miner_version)
            and not self.miner_install.blocked(release.version)
        )

    def _scheduled_miner_check(self):
        self._start_miner_update_job(automatic=self.cfg.get("miner_auto_update") is True)

    def _check_miner_update(self):
        self._start_miner_update_job()

    def _download_miner_update(self):
        if self._miner_update_available():
            self._start_miner_update_job(release=self.miner_release)

    def _start_miner_update_job(self, release: Release | None = None, automatic: bool = False):
        if self.miner_update_busy or self._quitting:
            return
        self.miner_update_busy = True
        self._set_update_status("正在准备下载…" if release else "正在检查官方版本…")
        install = self.miner_install
        results = self._miner_results
        proxy = str(self.cfg.get("miner_update_proxy") or "").strip()

        def run():
            try:
                if release is None:
                    results.put(("checked", install, automatic, check_release(proxy)))
                else:
                    def progress(status):
                        results.put(("progress", install, automatic, status))

                    install.stage(release, progress, proxy)
                    results.put(("ready", install, automatic, release))
            except Exception as exc:
                blocked = release if isinstance(exc, (ValueError, BadZipFile)) else None
                results.put(("error", install, automatic, (blocked, "下载" if release else "检查更新", str(exc))))

        threading.Thread(target=run, name="SRBMiner update", daemon=True).start()

    def _poll_miner_update(self):
        status = None
        while True:
            try:
                event, install, automatic, data = self._miner_results.get_nowait()
            except Empty:
                break
            if event == "progress":
                if install is self.miner_install:
                    status = data
                continue
            self.miner_update_busy = False
            if install is not self.miner_install:
                self._set_update_status("矿工路径已修改，请重新检查")
                return
            if event == "checked":
                self.miner_version = binary_version(install.executable)
                self.miner_release = release = data
                if self._miner_update_available():
                    self._set_update_status(f"可更新至 SRBMiner {release.version}")
                    if automatic and self.cfg.get("miner_auto_update") is True:
                        self._start_miner_update_job(release, automatic=True)
                elif not self.miner_version:
                    self._set_update_status("无法识别当前 SRBMiner 版本")
                elif version_key(release.version) <= version_key(self.miner_version):
                    self._set_update_status("当前矿工已是最新或更高版本")
                elif install.blocked(release.version):
                    self._set_update_status(f"版本 {release.version} 曾更新失败，等待新版本")
                else:
                    self._set_update_status("官方 Windows x64 ZIP 无可验证的 SHA256")
            elif event == "ready":
                if automatic and self.cfg.get("miner_auto_update") is not True:
                    self._set_update_status("自动更新已关闭，可以手动更新")
                else:
                    self._switch_miner(data)
            else:
                blocked, action, error = data
                if blocked:
                    try:
                        install.record_failure(blocked.version)
                    except OSError as exc:
                        self._log(f"无法记录矿工更新失败：{exc}")
                message = f"SRBMiner {action}失败，继续使用当前版本：{error}"
                self._set_update_status(message)
                self._log(message)
            return
        if status is not None:
            self._set_update_status(status)

    def _switch_miner(self, release: Release):
        if self.want_run and self.running_install not in (None, self.miner_install):
            self._set_update_status("请先重启矿工以应用已修改的矿工路径")
            return
        try:
            self.miner_install.activate(release.version)
        except OSError as exc:
            self._set_update_status(f"无法切换矿工，继续使用当前版本：{exc}")
            return
        self._trial_write_failed = False
        self.miner_version = release.version
        message = f"已切换到 SRBMiner {release.version}，等待运行验证"
        self._set_update_status(message)
        self._log(message)
        if self.want_run:
            self._kill_proc()
            self.pending_restart_at = None
            self._start_miner()

    def _miner_fault(self, reason: str):
        install = self.running_install
        if install and install.trial:
            warning = ""
            try:
                install.rollback()
            except OSError as exc:
                warning = f"；无法保存回滚状态：{exc}"
            if install is self.miner_install:
                self.miner_version = binary_version(install.executable)
                self._set_update_status(f"新版异常，已回滚至 {self.miner_version or '旧版'}{warning}")
            self._log(f"已回滚矿工：{reason}{warning}")
        self._restart(reason)

    # ── 启动/停止 ───────────────────────────────────────────────────
    def _mining_allowed(self) -> bool:
        return self.cfg.get("mining_authorized") is True and bool(self._wallet().partition("/")[0].strip())
    def _in_run_window(self) -> bool:
        """按定时设置判断当前是否属于允许运行的时间段。"""
        if not self.cfg.get("sched_enable"):
            return True
        try:
            sh, sm = [int(x) for x in str(self.cfg.get("sched_start", "")).split(":")[:2]]
            eh, em = [int(x) for x in str(self.cfg.get("sched_stop", "")).split(":")[:2]]
        except (TypeError, ValueError):
            return True
        now = datetime.now()
        cur = now.hour * 60 + now.minute
        a = sh * 60 + sm
        b = eh * 60 + em
        if a == b:
            return True
        return (a <= cur < b) if a < b else (cur >= a or cur < b)

    def _auto_boot(self):
        """程序启动后自动开挖，但必须尊重定时时段。"""
        if self.want_run or not self._exe_ok():
            return
        if not self._mining_allowed():
            self._log("请在设置中填写自己的收款账户，再点击「启动守护」确认设备授权")
            return
        if not self._in_run_window():
            self._log(
                f"程序已启动，但当前在定时停止时段"
                f"（{self.cfg.get('sched_stop')}–{self.cfg.get('sched_start')}），不自动开挖"
            )
            return
        self._log("程序启动，自动启动 Pearl 算力引擎")
        self._start_miner()

    def _on_toggle(self):
        if self.want_run:
            self._stop_all("手动停止")
        elif self._mining_allowed() or self._confirm_mining():
            self._start_miner()
        else:
            self.btn_run.setChecked(False)

    def _confirm_mining(self) -> bool:
        account = self._wallet().partition("/")[0].strip()
        if not account:
            QMessageBox.information(self, APP_NAME, "请先在「设置」中填入自己的收款账户。")
            return False
        choice = QMessageBox.question(
            self, "确认启动 Pearl 算力引擎",
            f"请确认设备所有者同意在本机使用 SRBMiner 参与 Pearl (PRL) 工作量计算，且收款账户 {account} 属于你。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return False
        authorized = {**self.cfg, "mining_authorized": True}
        try:
            save_config(authorized)
        except OSError as exc:
            QMessageBox.warning(self, "设置未保存", str(exc))
            return False
        self.cfg = authorized
        return True

    def _start_miner(self):
        if not self._mining_allowed():
            self.want_run = False
            self.btn_run.setChecked(False)
            return
        if not self._exe_ok():
            self._log("未配置有效的矿工程序，请先点「设置」")
            self.btn_run.setChecked(False)
            return

        self.running_install = self.miner_install
        self._log_path_from_exe(self.cfg["miner_exe"])
        # 本次运行独立日志：份额统计从这里面数，重启就重新计数
        try:
            miner_exe = resolve_path(self.cfg["miner_exe"])
            log_dir = miner_exe.parent / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            self.session_log = log_dir / f"srb-{datetime.now():%Y%m%d-%H%M%S}.log"
        except Exception:
            self.session_log = None
        self.share_stat = {"accepted": 0, "rejected": 0, "stale": 0, "lat_sum": 0.0, "lat_n": 0}
        self._log_pos = 0
        self._reset_share_metrics()
        cmd = self._build_cmd()
        # Relative paths in existing extra arguments still resolve from the configured miner directory.
        work_dir = self.miner_install.base.parent
        cwd = str(work_dir if work_dir.is_dir() else self.miner_install.executable.parent)

        # 启动前清掉所有残留实例，防止多开抢算力
        stale = kill_miners()
        if stale:
            self._log(f"启动前清理了 {stale} 个残留算力引擎进程")

        try:
            # 新建但隐藏的控制台：SRBMiner 需要控制台，但窗口不可见，
            # 也就不存在 QuickEdit 选中导致界面卡死的问题
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            self.proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                startupinfo=si,
            )
        except Exception as e:  # noqa: BLE001
            self._log(f"启动失败：{e}")
            self.btn_run.setChecked(False)
            if self.miner_install.trial:
                self.want_run = True
                self._miner_fault("新版矿工启动失败")
            return

        extra = kill_miners(keep_pid=self.proc.pid)
        if extra:
            self._log(f"启动后清理了 {extra} 个重复实例")

        self.want_run = True
        self.running = True
        self.started_at = time.time()
        self.grace_until = time.time() + 45  # 内核编译期，不算假死
        self.pool_wait = False
        self.api_fails = 0
        self.zero_streak = 0
        self.last_mining_time = None
        self.stale_count = 0
        self.btn_run.setChecked(True)
        self.btn_run.setText("停止守护")
        self._log(f"启动矿工 PID {self.proc.pid}（隐藏控制台）")
        self._start_poller()

    def _start_poller(self):
        if self.poller and self.poller.isRunning():
            return
        self.poller = ApiPoller(int(self.cfg.get("api_port", 21550)), 2.0, self)
        self.poller.got.connect(self._on_api)
        self.poller.start()

    def _stop_all(self, reason: str):
        was_running = self.want_run or self.proc is not None
        self.want_run = False
        self.running = False
        self.pending_restart_at = None
        self._kill_proc()
        # 不管是不是本程序拉起的，一律清干净，避免多开继续吃算力
        leftover = kill_miners() if was_running else 0
        self.btn_run.setChecked(False)
        self.btn_run.setText("启动守护")
        self._set_status("已停止", "#8A8A95")
        self.lb_hash.setText("—")
        self.lb_hash_sub.setText("1 分钟 —   ·   15 分钟 —")
        self.lb_uptime.setText("—")
        if hasattr(self, "chart"):
            self.chart.clear()
        for k in self.metrics:
            self.metrics[k].setText("—")
        self._update_share_metrics()  # 份额数字保留，方便看本轮战绩
        self._log(reason)
        if leftover:
            self._log(f"已清理 {leftover} 个残留算力引擎进程")

    def _kill_proc(self):
        p = self.proc
        self.proc = None
        if not p:
            return
        try:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    p.kill()
        except Exception:
            try:
                p.kill()
            except Exception:
                pass

    def _manual_restart(self):
        if not self.want_run:
            self._log("守护未启动，先点「启动守护」")
            return
        self._log("手动重启矿工")
        self._restart("手动重启")

    def _restart(self, reason: str):
        self._kill_proc()
        self.restarts += 1
        self.lb_hash.setText("—")
        self.lb_hash_sub.setText("1 分钟 —   ·   15 分钟 —")
        self._set_status("重启中…", "#C8871A")
        delay = int(self.cfg.get("restart_delay", 5))
        self._log(f"将在 {delay} 秒后重启（原因：{reason}）")
        self.pending_restart_at = time.time() + delay

    # ── 定时启停 ────────────────────────────────────────────────────
    def _schedule_due(self, hhmm: str, key: str) -> bool:
        """目标时刻起 2 分钟内算命中，且同一天只触发一次。"""
        try:
            h, m = [int(x) for x in str(hhmm).split(":")[:2]]
        except Exception:
            return False
        now = datetime.now()
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        delta = (now - target).total_seconds()
        if not (0 <= delta < 120):
            return False
        if self.sched_fired.get(key) == now.date():
            return False
        self.sched_fired[key] = now.date()
        return True

    def _check_schedule(self):
        if not self.cfg.get("sched_enable") or not self._mining_allowed():
            return
        if self._schedule_due(self.cfg.get("sched_start", ""), "start"):
            if not self.want_run:
                self._log("定时启动：启动 Pearl 算力引擎")
                self._start_miner()
            else:
                self._log("定时启动：已在运行，跳过")
        if self._schedule_due(self.cfg.get("sched_stop", ""), "stop"):
            if self.want_run:
                self._log("定时停止：停止 Pearl 算力引擎")
                self._stop_all("定时停止")
            else:
                self._log("定时停止：本来就没在跑")

    def _update_sched_label(self):
        if not hasattr(self, "lb_sched"):
            return
        if self.cfg.get("sched_enable"):
            self.lb_sched.setText(
                f"定时 {self.cfg.get('sched_start')} 启动 · {self.cfg.get('sched_stop')} 停止"
            )
        else:
            self.lb_sched.setText("定时未启用")

    # ── 每秒巡检 ────────────────────────────────────────────────────
    def _tick(self):
        self._check_schedule()
        self._poll_miner_update()
        if self.pending_restart_at and time.time() >= self.pending_restart_at:
            self.pending_restart_at = None
            if self.want_run:
                self._start_miner()

        if not self.want_run:
            return

        # 正在等待重启时，接口必然不通，清空计数避免叠加判定
        if self.pending_restart_at is not None:
            self.api_fails = 0
            self.zero_streak = 0
            return

        # 进程死了
        if self.proc is not None and self.proc.poll() is not None:
            code = self.proc.returncode
            self.proc = None
            self._log(f"矿工进程已退出，退出码 {code}")
            self._miner_fault(f"进程退出 {code}")
            return

        # 只允许一个实例：多余的直接杀掉，避免多开抢算力
        if self.proc is not None:
            try:
                extras = [p for p in list_miner_pids() if p != self.proc.pid]
            except Exception:
                extras = []
            if extras:
                n = kill_miners(keep_pid=self.proc.pid)
                self._log(f"发现 {len(extras)} 个多余算力引擎实例，已清理 {n} 个")

        # 运行时长
        if self.started_at:
            sec = int(time.time() - self.started_at)
            h, rem = divmod(sec, 3600)
            m, s = divmod(rem, 60)
            self.lb_uptime.setText(f"已运行 {h:02d}:{m:02d}:{s:02d} · 重启 {self.restarts} 次")

        # API 连续失败 → 判为假死（启动前 45 秒内核编译期豁免）
        in_grace = time.time() < self.grace_until
        if not in_grace and self.api_fails >= int(self.cfg.get("api_fail_limit", 3)):
            self._log(f"统计接口连续 {self.api_fails} 次无响应，判定假死")
            self.api_fails = 0
            self._miner_fault("统计接口无响应")
            return

        # 算力长时间为 0 → 判为假死；但如果只是矿池断线，重启没用，等它自己重连
        if not in_grace and not self.pool_wait and self.zero_streak >= int(
            self.cfg.get("zero_hash_limit", 60)
        ):
            self._log(f"算力连续 {self.zero_streak} 次为 0，判定假死")
            self.zero_streak = 0
            self._miner_fault("算力为 0")
            return

        # 心跳不前进 → 进程还在但主循环卡死（就是你按回车才恢复的那种）
        # 注意：mining_time 更新粒度约 10 秒，直接判会误杀，所以只在
        # API 持续有响应但算力长期为 0 时才重启，见上面的 zero_streak。

    # ── API 回调 ────────────────────────────────────────────────────
    def _on_api(self, payload: dict):
        if not payload.get("ok"):
            # 矿工没在跑/正在等重启时，接口本来就不通，不能算假死
            if (
                self.want_run
                and self.proc is not None
                and self.pending_restart_at is None
                and time.time() >= self.grace_until
            ):
                self.api_fails += 1
            return

        self.api_fails = 0
        d = payload["data"]
        self.stats = d

        try:
            algo = (d.get("algorithms") or [{}])[0]
            hr = algo.get("hashrate", {}) or {}
            h1 = float(hr.get("1min") or 0)
            h15 = float(hr.get("15min") or 0)
            gpu = (d.get("gpu_devices") or [{}])[0]
            shares = algo.get("shares", {}) or {}

            self.lb_hash.setText(f"{h1 / 1e12:.2f} TH/s")
            self.chart.add_sample(h1 / 1e12)
            self.lb_hash_sub.setText(
                f"1 分钟 {h1 / 1e12:.2f}   ·   15 分钟 {h15 / 1e12:.2f}"
            )
            self.metrics["temp"].setText(f"{gpu.get('temperature', '—')} °C")
            power = gpu.get("asic_power")
            self.metrics["power"].setText(
                f"{power:.0f} W" if isinstance(power, (int, float)) else "— W"
            )
            # 份额数字由日志解析得出（更准，能区分 stale），这里不再覆盖

            # 心跳只做参考，不参与判定（mining_time 粒度粗，直接判会误杀）
            in_grace = time.time() < self.grace_until
            mt = d.get("mining_time")
            if isinstance(mt, (int, float)):
                if in_grace:
                    self.stale_count = 0
                self.last_mining_time = int(mt)

            pool_obj = algo.get("pool") or {}
            pool = pool_obj.get("pool", "—")
            self.lb_pool.setText(
                f"矿池 {pool} · 矿工 {self.cfg.get('worker', '—')} · API {self.cfg.get('api_port')}"
            )

            # 判断是否只是矿池断线：last_job_received 是「距上一个任务多少秒」
            try:
                jr = float(pool_obj.get("last_job_received") or 0)
            except (TypeError, ValueError):
                jr = 0.0
            self.pool_wait = jr > 120 and h1 <= 0

            if self.want_run:
                if self.pool_wait:
                    self._set_status("等待矿池", "#C8871A")
                else:
                    self._set_status("运行中", "#2FA84F")
                if in_grace:
                    self.zero_streak = 0
                else:
                    self.zero_streak = self.zero_streak + 1 if h1 <= 0 else 0
                install = self.running_install
                if (
                    install and install.trial and not self._trial_write_failed and self.proc
                    and self.started_at and h1 > 0 and time.time() - self.started_at >= 120
                ):
                    try:
                        install.confirm()
                    except OSError as exc:
                        self._trial_write_failed = True
                        self._log(f"无法保存新版矿工验证结果：{exc}")
                    else:
                        if install is self.miner_install:
                            self._set_update_status(f"SRBMiner {self.miner_version} 已通过运行验证")
            else:
                self._set_status("外部实例运行中", "#0A84FF")
        except Exception:
            pass

    def _set_status(self, text: str, color: str):
        self.lb_status.setText(text)
        self.lb_status.setStyleSheet(
            f"font-size:15px; font-weight:600; color:{color};"
        )
        self.lb_dot.setStyleSheet(f"font-size:15px; color:{color};")

    # ── 日志 ────────────────────────────────────────────────────────
    def _log(self, text: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.txt_log.appendPlainText(f"[{ts}] {text}")
        bar = self.txt_log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _refresh_log(self):
        """增量读取本次会话日志：解析份额事件 + 回显份额/错误行。"""
        if not self.session_log or not self.session_log.exists():
            return
        try:
            with open(self.session_log, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._log_pos)
                chunk = f.read()
                self._log_pos = f.tell()
        except Exception:
            return
        if not chunk:
            return

        for line in chunk.splitlines():
            s = line.strip()
            if not s:
                continue
            self._parse_share_line(s)
            low = s.lower()
            if "share" in low or "error" in low:
                self.txt_log.appendPlainText(f"  {s[:150]}")
        bar = self.txt_log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _parse_share_line(self, s: str):
        st = self.share_stat
        if "share accepted" in s:
            st["accepted"] += 1
            m = re.search(r"\[\s*(\d+)ms\s*\]", s)
            if m:
                st["lat_sum"] += int(m.group(1))
                st["lat_n"] += 1
        elif "share rejected" in s:
            st["rejected"] += 1
            if "stale" in s:
                st["stale"] += 1
        else:
            return
        self._update_share_metrics()

    def _update_share_metrics(self):
        st = self.share_stat
        if "accepted" in self.metrics:
            self.metrics["accepted"].setText(str(st["accepted"]))
            self.metrics["rejected"].setText(str(st["rejected"]))
            self.metrics["stale"].setText(str(st["stale"]))
            if st["lat_n"]:
                avg = st["lat_sum"] / st["lat_n"]
                self.metrics["latency"].setText(f"{avg:.0f} ms")
            else:
                self.metrics["latency"].setText("— ms")

    def _reset_share_metrics(self):
        self.share_stat = {
            "accepted": 0,
            "rejected": 0,
            "stale": 0,
            "lat_sum": 0.0,
            "lat_n": 0,
        }
        self._update_share_metrics()

    def _refresh_hourly(self):
        exe = self.cfg.get("miner_exe", "")
        if not exe:
            return
        log_dir = resolve_path(exe).parent / "logs"
        mode = getattr(self, "hourly_mode", "hour")
        try:
            self.hourly.set_data(scan_buckets(log_dir, mode))
        except Exception:
            pass

    def _on_period_changed(self, idx: int):
        self.hourly_mode = ["hour", "day", "month"][idx]
        self._refresh_hourly()

    def closeEvent(self, e):
        if not self._quitting and self.cfg.get("close_to_tray", True) and QSystemTrayIcon.isSystemTrayAvailable():
            self._hide_to_tray()
            e.ignore()
            return
        self._quitting = True
        self.hotkey.set_enabled(False)
        QApplication.instance().removeNativeEventFilter(self.hotkey)
        self.tray.hide()
        self._stop_all("窗口关闭，已停止守护")
        if self.poller:
            self.poller.stop()
            self.poller.wait(1500)
        e.accept()
        QApplication.instance().quit()


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setFont(QFont(pick_font(), 10))
    app.setWindowIcon(make_app_icon())
    app.setQuitOnLastWindowClosed(False)
    # 重复点击新版快捷方式只呼出现有窗口，避免两个守护器互相重启矿工。
    server_name = "Jack.PearlGuard.Supervisor"
    client = QLocalSocket()
    client.connectToServer(server_name)
    if client.waitForConnected(500):
        client.disconnectFromServer()
        return 0
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(server_name):
        QLocalServer.removeServer(server_name)
        if not server.listen(server_name):
            QMessageBox.warning(None, APP_NAME, "无法启动守护器，请检查是否已有新版在运行。")
            return 1
    w = MainWindow()
    def show_existing():
        connection = server.nextPendingConnection()
        if connection is not None:
            connection.disconnectFromServer()
            connection.deleteLater()
        w._show_from_tray()
    server.newConnection.connect(show_existing)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

