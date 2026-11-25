"""Check and stage official Windows SRBMiner releases without stopping the miner."""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from zipfile import ZipFile

MINER_EXE = "SRBMiner-MULTI.exe"
MINER_FILES = {name.casefold(): name for name in (MINER_EXE, "ReadMe.txt", "WinRing0x64.sys")}
RELEASE_URL = "https://api.github.com/repos/doktor83/SRBMiner-Multi/releases/latest"
ASSET_DIR = "/doktor83/SRBMiner-Multi/releases/download/"
CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000
MAX_DOWNLOAD = 256 * 1024 * 1024
VERSION_RE = re.compile(r"v?(\d{1,5})[.-](\d{1,5})[.-](\d{1,5})", re.I)
WINDOWS_ZIP = re.compile(r"srbminer-multi-(.+)[-_.](?:win(?:dows)?[-_.]?(?:x64|64|amd64))\.zip", re.I)


def version_key(value: str) -> tuple[int, int, int]:
    match = VERSION_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"无法识别正式版本号：{value}")
    return tuple(map(int, match.groups()))


def binary_version(path: Path) -> str:
    """Read the Windows version resource without executing a miner."""
    if os.name != "nt" or not path.is_file() or path.name.casefold() != MINER_EXE.casefold():
        return ""
    api = ctypes.WinDLL("version")
    api.GetFileVersionInfoSizeW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD))
    api.GetFileVersionInfoW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    )
    api.VerQueryValueW.argtypes = (
        ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.UINT),
    )
    size = api.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return ""
    data = ctypes.create_string_buffer(size)
    value = ctypes.c_void_p()
    length = wintypes.UINT()
    if not api.GetFileVersionInfoW(str(path), 0, size, data):
        return ""
    if not api.VerQueryValueW(data, "\\", ctypes.byref(value), ctypes.byref(length)):
        return ""
    if length.value < 6 * ctypes.sizeof(wintypes.DWORD):
        return ""
    fixed = ctypes.cast(value, ctypes.POINTER(wintypes.DWORD))
    if fixed[0] != 0xFEEF04BD:
        return ""
    ms, ls = fixed[2], fixed[3]  # VS_FIXEDFILEINFO.dwFileVersionMS/LS
    return f"{ms >> 16}.{ms & 0xffff}.{ls >> 16}"


@dataclass(frozen=True)
class Release:
    version: str
    url: str = ""
    sha256: str = ""
    size: int = 0


def _windows_curl() -> Path | None:
    if os.name != "nt":
        return None
    exe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "curl.exe"
    return exe if exe.is_file() else None


@contextmanager
def _windows_download(exe: Path, url: str, headers: dict[str, str], proxy: str = ""):
    # Windows curl uses the OS trust store and streams the body to the update worker.
    args = [
        str(exe), "--disable", "--noproxy", "" if proxy else "*",
        "--proto", "=https", "--proto-redir", "=https",
        "--fail", "--silent", "--show-error", "--location", "--connect-timeout", "12",
        "--speed-limit", "1", "--speed-time", "30", "--max-time", "1800",
        "--user-agent", headers["User-Agent"], "--header", f"Accept: {headers['Accept']}", url,
    ]
    if proxy:
        args[2:2] = ["--proxy", proxy]
    with subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW,
    ) as process:
        try:
            yield process.stdout
            if process.wait(timeout=5):
                detail = process.stderr.read(2048).decode(errors="replace").strip()
                raise urllib.error.URLError(detail or "GitHub 连接失败")
        finally:
            if process.poll() is None:
                process.kill()


def _request(url: str, proxy: str = ""):
    accept = "application/vnd.github+json" if url == RELEASE_URL else "application/octet-stream"
    headers = {"User-Agent": "PearlGuard/2.3", "Accept": accept}
    curl = _windows_curl()
    if proxy:
        if curl:
            return _windows_download(curl, url, headers, proxy)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy}))
        return opener.open(urllib.request.Request(url, headers=headers), timeout=20)
    try:
        return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15)
    except urllib.error.HTTPError as exc:
        if exc.code not in (407, 408, 429, 500, 502, 503, 504):
            raise
        exc.close()
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        pass
    if curl:
        return _windows_download(curl, url, headers)
    # The proxy handler mutates its Request, so send a fresh one for the direct connection.
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return direct.open(urllib.request.Request(url, headers=headers), timeout=20)


def check_release(proxy: str = "") -> Release:
    with _request(RELEASE_URL, proxy) as response:
        data = json.loads(response.read(2 * 1024 * 1024))
    if data.get("draft") is not False or data.get("prerelease") is not False:
        raise ValueError("GitHub 没有返回正式发布版")
    tag = data["tag_name"]
    version = version_key(tag)
    version_text = ".".join(map(str, version))
    for asset in data["assets"]:
        name = asset.get("name", "")
        match = WINDOWS_ZIP.fullmatch(name)
        if not match or not VERSION_RE.fullmatch(match[1]) or version_key(match[1]) != version:
            continue
        url = asset.get("browser_download_url", "")
        parsed = urllib.parse.urlsplit(url)
        expected_path = ASSET_DIR + f"{tag}/{name}"
        size = asset.get("size", 0)
        digest = re.fullmatch(r"sha256:([a-f0-9]{64})", asset.get("digest") or "", re.I)
        if (
            parsed.scheme == "https" and parsed.netloc.lower() == "github.com"
            and urllib.parse.unquote(parsed.path).casefold() == expected_path.casefold()
            and not parsed.query and not parsed.fragment
            and type(size) is int and 0 < size <= MAX_DOWNLOAD and digest
        ):
            return Release(version_text, url, digest[1].lower(), size)
    return Release(version_text)


class MinerInstall:
    """Keep the configured executable and its logs in place. Switch versions via one file."""

    def __init__(self, root: Path, base: Path):
        self.base = base.resolve()
        key = hashlib.sha256(os.path.normcase(str(self.base)).encode("utf-8")).hexdigest()[:20]
        self.root = root / key
        self.state_file = self.root / "state.json"
        self.state = self._load()

    def _load(self) -> dict:
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            for key in ("active", "previous"):
                value = state.setdefault(key, "")
                if not isinstance(value, str) or (value and not VERSION_RE.fullmatch(value)):
                    raise ValueError("invalid version")
            if state.get("trial") and not state["active"]:
                raise ValueError("missing trial version")
            if not isinstance(state.get("failed", []), list):
                raise ValueError("invalid failed versions")
            return state
        except (OSError, ValueError, TypeError, AttributeError):
            return {}

    def _save(self, state: dict):
        self.root.mkdir(parents=True, exist_ok=True)
        temp = self.state_file.with_suffix(".tmp")
        temp.write_text(json.dumps(state), encoding="utf-8")
        temp.replace(self.state_file)
        self.state = state

    def _path(self, version: str) -> Path:
        return self.root / version / MINER_EXE if version else self.base

    @property
    def executable(self) -> Path:
        for key in ("active", "previous"):
            path = self._path(self.state.get(key, ""))
            if path.is_file():
                return path
        return self.base

    @property
    def trial(self) -> bool:
        return bool(self.state.get("trial")) and self.executable == self._path(self.state["active"])

    def blocked(self, version: str) -> bool:
        return version in self.state.get("failed", [])

    def record_failure(self, version: str):
        if not self.blocked(version):
            self._save({**self.state, "failed": [*self.state.get("failed", []), version]})

    def activate(self, version: str):
        if not self._path(version).is_file():
            raise OSError("新版矿工文件不存在")
        current_path = self.executable
        previous = "" if current_path == self.base else current_path.parent.name
        if self.trial:
            previous = self.state["previous"]
        self._save({**self.state, "active": version, "previous": previous, "trial": True})

    def confirm(self):
        self._save({**self.state, "trial": False})

    def rollback(self):
        active = self.state["active"]
        previous = self.state["previous"]
        failed = list(dict.fromkeys([*self.state.get("failed", []), active]))
        state = {"active": previous, "previous": "", "trial": False, "failed": failed}
        self.state = state  # Recover the running session even if the disk is no longer writable.
        self._save(state)

    def stage(self, release: Release, progress, proxy: str = ""):
        if not release.url or not release.sha256:
            raise ValueError("官方 Windows x64 ZIP 缺少 SHA256")
        self.root.mkdir(parents=True, exist_ok=True)
        # Only the running app creates these folders. Retain both usable versions.
        keep = {release.version, self.state.get("active"), self.state.get("previous")}
        for path in self.root.iterdir():
            if path.is_dir() and (path.name.startswith("download-") or (
                re.fullmatch(r"\d{1,5}(?:\.\d{1,5}){2}", path.name) and path.name not in keep
            )):
                shutil.rmtree(path, ignore_errors=True)

        with tempfile.TemporaryDirectory(dir=self.root, prefix="download-") as temp:
            folder = Path(temp)
            archive = folder / "miner.zip"
            sha256 = hashlib.sha256()
            read = 0
            with _request(release.url, proxy) as response, archive.open("wb") as out:
                while chunk := response.read(256 * 1024):
                    read += len(chunk)
                    if read > release.size:
                        raise urllib.error.URLError("下载大小超过官方发布记录")
                    sha256.update(chunk)
                    out.write(chunk)
                    progress(f"正在下载 SRBMiner {release.version} · {read * 100 // release.size}%")
            if read != release.size:
                raise urllib.error.URLError("下载未完成")
            if sha256.hexdigest() != release.sha256:
                raise ValueError("SHA256 校验失败")

            progress("SHA256 校验通过，正在解压…")
            package = folder / "package"
            package.mkdir()
            with ZipFile(archive) as zip_file:
                for item in zip_file.infolist():
                    name = MINER_FILES.get(Path(item.filename).name.casefold())
                    if name is None or item.is_dir():
                        continue
                    if item.file_size > MAX_DOWNLOAD:
                        raise ValueError("解压文件超过大小限制")
                    with zip_file.open(item) as source, (package / name).open("xb") as target:
                        shutil.copyfileobj(source, target)
            if binary_version(package / MINER_EXE) != release.version:
                raise ValueError("ZIP 中矿工的版本与官方发布版本不符")
            destination = self.root / release.version
            if destination.exists():
                shutil.rmtree(destination)
            package.rename(destination)
