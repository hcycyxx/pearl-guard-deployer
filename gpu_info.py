"""不依赖第三方包的 Windows 显卡和电脑型号探测。"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _normalize_worker(name: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(name or "").strip()).strip("-._")
    return text[:40] or "PearlRig"


def worker_from_model(model: str) -> str:
    original = str(model or "").strip()
    text = re.sub(r"\(R\)|\(TM\)", "", original, flags=re.I)
    text = re.sub(r"\bNVIDIA\b|\bGEFORCE\b|\bAMD\b|\bRADEON\b", "", text, flags=re.I)
    text = re.sub(r"\bGRAPHICS\b|\bGPU\b|\bLAPTOP\b", "", text, flags=re.I)
    text = re.sub(r"\bRTX\s*", "RTX", text, flags=re.I)
    text = re.sub(r"\bGTX\s*", "GTX", text, flags=re.I)
    text = re.sub(r"\bRX\s*", "RX", text, flags=re.I)
    return _normalize_worker(re.sub(r"\s+", "", text) or original)


def _registry_video_names() -> list[str]:
    try:
        import winreg
    except ImportError:
        return []
    names: list[str] = []
    root = r"HARDWARE\DEVICEMAP\VIDEO"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root) as key:
            for i in range(32):
                try:
                    child = winreg.EnumKey(key, i)
                    with winreg.OpenKey(key, child) as device:
                        value, _ = winreg.QueryValueEx(device, "Device Description")
                        if value and value not in names:
                            names.append(str(value))
                except OSError:
                    break
    except OSError:
        pass
    return names


def _cim_names() -> tuple[list[str], str]:
    script = "\n".join([
        "$ProgressPreference='SilentlyContinue'",
        "$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)",
        "$g=@(Get-CimInstance Win32_VideoController | Select-Object Name)",
        "$c=Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model",
        "[pscustomobject]@{gpus=$g;system=$c}|ConvertTo-Json -Depth 3 -Compress",
    ])
    import base64
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    try:
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=15, creationflags=NO_WINDOW,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return [], ""
        raw = json.loads(result.stdout.strip().splitlines()[-1])
        gpus = raw.get("gpus") or []
        if isinstance(gpus, dict):
            gpus = [gpus]
        system = raw.get("system") or {}
        if isinstance(system, list):
            system = system[0] if system else {}
        machine = f"{system.get('Manufacturer', '')} {system.get('Model', '')}".strip()
        return [str(g.get("Name") or "") for g in gpus if isinstance(g, dict)], machine
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return [], ""


def detect() -> dict:
    names = _registry_video_names()
    machine = ""
    if not any(re.search(r"NVIDIA|AMD|Radeon", n, re.I) for n in names):
        cim_names, machine = _cim_names()
        for name in cim_names:
            if name and name not in names:
                names.append(name)

    candidates = []
    for name in names:
        name = name.strip()
        if not name or re.search(r"virtual|remote|basic display|microsoft", name, re.I):
            continue
        vendor = "NVIDIA" if "nvidia" in name.lower() else "AMD" if re.search(
            r"amd|radeon", name, re.I
        ) else "Intel" if "intel" in name.lower() else ""
        candidates.append({"name": name, "vendor": vendor, "worker": worker_from_model(name)})
    candidates.sort(key=lambda g: 0 if g["vendor"] == "NVIDIA" else 1 if g["vendor"] == "AMD" else 2)
    primary = candidates[0] if candidates else None
    fallback = _normalize_worker(machine)
    return {
        "gpu_name": primary["name"] if primary else "",
        "vendor": primary["vendor"] if primary else "",
        "worker": primary["worker"] if primary else fallback,
        "fallback_worker": fallback,
        "gpus": candidates,
    }
