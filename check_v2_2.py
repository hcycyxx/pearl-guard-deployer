"""V2.2.0 离线检查：不启动矿工，不修改显卡参数。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gpu_info
import pearl_guard as guard
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication, QScrollArea


app = QApplication([])
app.setFont(guard.QFont(guard.pick_font(), 10))

assert gpu_info.worker_from_model("NVIDIA GeForce RTX 3060 Laptop GPU") == "RTX3060"
assert gpu_info.worker_from_model("AMD Radeon RX 6700 XT") == "RX6700XT"
assert gpu_info.worker_from_model("NVIDIA GeForce RTX 4090") == "RTX4090"
detected = gpu_info.detect()
assert detected["worker"]

assert guard.DEFAULT_CONFIG["miner_exe"] == "miner\\SRBMiner-MULTI.exe"
assert guard.DEFAULT_CONFIG["fan"] == 0
assert guard.DEFAULT_CONFIG["power_limit"] == 0
assert guard.DEFAULT_CONFIG["core_offset"] == 0
assert guard.DEFAULT_CONFIG["mem_offset"] == 0

class CommandContext:
    _wallet = guard.MainWindow._wallet
    _build_cmd = guard.MainWindow._build_cmd
    session_log = None

    def __init__(self, cfg):
        self.cfg = dict(cfg)


ctx = CommandContext(guard.DEFAULT_CONFIG)
cmd = ctx._build_cmd()
assert "--gpu-fan0" not in cmd
assert "--gpu-plimit0" not in cmd
ctx.cfg["worker"] = "RTX3060"
cmd = ctx._build_cmd()
assert cmd[cmd.index("--wallet") + 1] == "krxX72P2ZE/RTX3060"
assert str(guard.resolve_path("miner\\SRBMiner-MULTI.exe")).endswith(
    "supervisor_v2_2_20261111\\miner\\SRBMiner-MULTI.exe"
)

with patch.object(guard.MainWindow, "_setup_tray"), \
     patch.object(guard.MainWindow, "_apply_hotkey"), \
     patch.object(guard.MainWindow, "_refresh_hourly"), \
     patch.object(guard.QTimer, "singleShot"), \
     patch.object(guard.GlobalHotkey, "set_enabled", return_value=True):
    window = guard.MainWindow()
    window.timer.stop()
    window.log_timer.stop()
    window.hourly_timer.stop()
    assert window.main_scroll is not None
    assert window.minimumHeight() == 520
    window.hide()
    app.removeNativeEventFilter(window.hotkey)

screen = QGuiApplication.primaryScreen().availableGeometry()
window.resize(min(760, screen.width() - 24), min(600, screen.height() - 24))
window.show()
app.processEvents()
assert window.width() <= max(720, screen.width() - 24)
assert window.height() <= max(520, screen.height() - 24)
window.grab().save("preview_main_768.png")
window.hide()

settings = guard.SettingsDialog(guard.DEFAULT_CONFIG)
settings.resize(640, min(600, screen.height() - 60))
settings.show()
app.processEvents()
assert settings.findChild(QScrollArea) is not None
settings.grab().save("preview_settings_768.png")
settings.reject()

report = {
    "version": guard.APP_VERSION,
    "detected_gpu": detected,
    "checks": [
        "NVIDIA 和 AMD 型号转换为矿工名",
        "显卡、功耗、核心和显存默认不设置",
        "矿工路径使用安装目录相对路径",
        "主窗口最小高度 520，小屏可滚动",
        "设置窗口可滚动",
        "768 高度界面预览",
    ],
    "not_tested": [
        "目标电脑全新安装",
        "不同显卡真实算力与温度",
        "真实 Alt+0 和托盘交互",
    ],
}
Path("check_v2_2_result.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(report, ensure_ascii=False))
