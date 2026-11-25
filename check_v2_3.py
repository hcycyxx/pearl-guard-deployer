"""V2.3 regressions with fake miner processes. Run using the PyQt6 build environment."""

import ctypes
from ctypes import wintypes
import hashlib
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import URLError
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import miner_update as update
import pearl_guard as guard


def fake_version(exe):
    return exe.read_text(encoding="ascii") if exe.is_file() else ""


def archive_contents(version):
    data = io.BytesIO()
    with ZipFile(data, "w") as archive:
        archive.writestr(f"SRBMiner/{update.MINER_EXE}", version)
        archive.writestr("SRBMiner/ReadMe.txt", "release notes")
        archive.writestr("../../outside.bat", "ignore")
    return data.getvalue()


def release_for(version, data):
    folder = version.replace(".", "-")
    url = f"https://github.com{update.ASSET_DIR}{version}/SRBMiner-Multi-{folder}-win64.zip"
    return update.Release(version, url, hashlib.sha256(data).hexdigest(), len(data))


class ReleaseTests(unittest.TestCase):
    def test_official_requests_retry_without_an_unreachable_proxy(self):
        for url in (update.RELEASE_URL, release_for("3.9.0", b"zip").url):
            with self.subTest(url=url):
                response = io.BytesIO(b"official response")
                def unavailable_proxy(request, **_):
                    request.set_proxy("127.0.0.1:10809", "http")
                    raise URLError("proxy offline")

                with (
                    patch.object(update.urllib.request, "urlopen", side_effect=unavailable_proxy),
                    patch.object(update, "_windows_curl", return_value=None),
                    patch.object(update.urllib.request, "build_opener") as opener,
                ):
                    opener.return_value.open.return_value = response
                    self.assertIs(update._request(url), response)
                    self.assertEqual(opener.call_args.args[0].proxies, {})
                    direct_request = opener.return_value.open.call_args.args[0]
                    self.assertEqual(direct_request.full_url, url)
                    self.assertNotEqual(direct_request.host, "127.0.0.1:10809")

    def test_official_assets_and_stable_version_order(self):
        self.assertGreater(update.version_key("3.10.0"), update.version_key("3.9.0"))
        release = release_for("3.10.0", b"zip")
        record = {
            "tag_name": release.version, "draft": False, "prerelease": False,
            "assets": [
                {"name": "SRBMiner-Multi-3-10-0-Linux.tar.gz"},
                {
                    "name": release.url.rsplit("/", 1)[1], "browser_download_url": release.url,
                    "size": release.size, "digest": f"sha256:{release.sha256}",
                },
            ],
        }
        with patch.object(update, "_request", side_effect=lambda *_: io.BytesIO(json.dumps(record).encode())):
            self.assertEqual(update.check_release(), release)
            record["assets"][1]["digest"] = None
            self.assertFalse(update.check_release().url)
            record["prerelease"] = True
            with self.assertRaises(ValueError):
                update.check_release()


class FakeMiner:
    pid = 1042

    def __init__(self, command):
        self.command = command
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -1

    kill = terminate

    def wait(self, timeout):
        return self.returncode


@unittest.skipUnless(os.name == "nt", "Windows process and hotkey paths")
class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = guard.QApplication.instance() or guard.QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)

    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.base = self.root / "miner" / update.MINER_EXE
        self.base.parent.mkdir()
        self.base.write_text("3.8.0", encoding="ascii")
        self.cfg_file = self.root / "supervisor.json"
        self.cfg = {
            **guard.DEFAULT_CONFIG, "worker": "TestGPU", "account": "krxTest",
            "mining_authorized": True, "restart_delay": 0, "fan": 43,
            "power_limit": 65, "core_offset": 100, "mem_offset": -200,
            "extra_args": "--gpu-id 0",
        }
        self.cfg_file.write_text(json.dumps(self.cfg), encoding="utf-8")
        self.spawn = self.mock(guard.subprocess, "Popen", side_effect=lambda cmd, **_: FakeMiner(cmd))
        self.kill = self.mock(guard, "kill_miners", return_value=0)
        self.mock(guard, "list_miner_pids", return_value=[])
        self.mock(guard, "CONFIG_FILE", new=self.cfg_file)
        self.mock(guard, "app_dir", return_value=self.root)
        self.mock(guard, "binary_version", side_effect=fake_version)
        self.mock(update, "binary_version", side_effect=fake_version)
        self.mock(guard.MainWindow, "_start_poller")
        self.mock(guard, "detect_gpu", return_value={"worker": "TestGPU", "gpu_name": "Test GPU"})
        self.mock(guard, "query_power_range", return_value=None)
        with patch.object(guard.QTimer, "singleShot"):
            self.win = guard.MainWindow()
        self.addCleanup(self.close_window)

    def mock(self, target, name, **kwargs):
        patcher = patch.object(target, name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def close_window(self):
        self.win._quit_app()
        self.qt.processEvents()

    def wait_update(self):
        deadline = time.monotonic() + 5
        while self.win.miner_update_busy and time.monotonic() < deadline:
            self.win._poll_miner_update()
            time.sleep(0.01)
        self.assertFalse(self.win.miner_update_busy, self.win.miner_update_status)

    def mock_release(self, version, corrupt=False):
        data = archive_contents(version)
        release = release_for(version, data)
        self.mock(guard, "check_release", return_value=release)
        if corrupt:
            data = archive_contents("6.0.0")
            self.assertEqual(len(data), release.size)
        self.mock(update, "_request", side_effect=lambda *_: io.BytesIO(data))
        return release

    def test_automatic_check_download_switch_and_healthy_version(self):
        self.mock_release("3.9.0")
        window = self.win
        self.assertEqual(window.update_timer.interval(), 6 * 60 * 60 * 1000)
        window.cfg["miner_auto_update"] = True
        window._start_miner()
        previous = window.proc
        initial_config = self.cfg_file.read_bytes()
        begin, proceed = threading.Event(), threading.Event()
        stage = window.miner_install.stage

        def wait_before_stage(release, progress, proxy=""):
            progress("正在下载 50%")
            begin.set()
            if not proceed.wait(5):
                raise RuntimeError("test download gate")
            stage(release, progress, proxy)

        with patch.object(window.miner_install, "stage", side_effect=wait_before_stage):
            window._scheduled_miner_check()
            for _ in range(100):
                window._poll_miner_update()
                if begin.wait(0.01):
                    break
            self.assertTrue(begin.is_set())
            try:
                window._tick()
                self.assertIs(window.proc, previous)
                self.assertIsNone(previous.poll())
                self.assertIn("50%", window.miner_update_status)
            finally:
                proceed.set()
            self.wait_update()
        self.assertEqual(window.miner_version, "3.9.0")
        self.assertTrue(window.miner_install.trial)
        self.assertNotEqual(window.proc.command[0], previous.command[0])
        self.assertEqual(window.proc.command[1:-1], previous.command[1:-1])
        self.assertEqual(window.session_log.parent, self.base.parent / "logs")
        self.assertEqual(self.spawn.call_args.kwargs["cwd"], str(self.base.parent))
        window.started_at = time.time() - 130
        window._on_api({"ok": True, "data": {"algorithms": [{"hashrate": {"1min": 1e12}}]}})
        self.assertFalse(window.miner_install.trial)
        self.assertTrue(self.base.exists())
        self.assertEqual(initial_config, self.cfg_file.read_bytes())

    def test_manual_update_while_stopped_and_rollback_on_early_exit(self):
        release = self.mock_release("3.9.0")
        window = self.win
        window._check_miner_update()
        self.wait_update()
        self.assertTrue(window._miner_update_available())
        window._download_miner_update()
        self.wait_update()
        self.spawn.assert_not_called()
        self.assertFalse(window.want_run)
        window._start_miner()
        window.proc.returncode = 1
        window._tick()
        window._tick()
        self.assertEqual(window.miner_version, "3.8.0")
        self.assertEqual(window.proc.command[0], str(self.base))
        self.assertFalse(window._miner_update_available())
        reloaded = update.MinerInstall(self.root / "miner-updates", self.base)
        self.assertTrue(reloaded.blocked(release.version))

    def test_watchdog_failure_rolls_back_and_preserves_previous_updated_version(self):
        window = self.win
        release = self.mock_release("3.9.0")
        window.miner_install.stage(release, lambda _: None)
        window._switch_miner(release)
        window.miner_install.confirm()
        data = archive_contents("4.0.0")
        release = release_for("4.0.0", data)
        with patch.object(update, "_request", return_value=io.BytesIO(data)):
            window.miner_install.stage(release, lambda _: None)
        window._switch_miner(release)
        window._start_miner()
        window.grace_until = 0
        window.api_fails = window.cfg["api_fail_limit"]
        window._tick()
        window._tick()
        self.assertEqual(window.miner_version, "3.9.0")
        self.assertEqual(Path(window.proc.command[0]).parent.name, "3.9.0")
        self.assertTrue(window.miner_install.blocked("4.0.0"))

    def test_invalid_sha_and_network_failure_keep_miner(self):
        self.mock_release("3.9.0", corrupt=True)
        window = self.win
        window.cfg["miner_auto_update"] = True
        window._start_miner()
        before = window.proc
        window._scheduled_miner_check()
        self.wait_update()
        self.assertIn("SHA256 校验失败", window.miner_update_status)
        self.assertIs(window.proc, before)
        self.assertFalse(window._miner_update_available())
        with patch.object(guard, "check_release", side_effect=URLError("offline")):
            window._scheduled_miner_check()
            self.wait_update()
        self.assertIn("offline", window.miner_update_status)
        self.assertIs(window.proc, before)
        self.assertIsNone(before.poll())

    def test_launch_failure_rolls_back_without_starting_twice(self):
        release = self.mock_release("3.9.0")
        window = self.win
        window.miner_install.stage(release, lambda _: None)
        window._switch_miner(release)
        self.spawn.side_effect = [OSError("failed launch"), FakeMiner([str(self.base)])]
        window._start_miner()
        self.assertEqual(window.miner_version, "3.8.0")
        window._tick()
        self.assertEqual(self.spawn.call_count, 2)
        self.assertEqual(window.proc.command, [str(self.base)])

    def test_settings_remain_interactive_while_gpu_probe_is_blocked(self):
        probe_started = threading.Event()
        release_probe = threading.Event()
        probe_timeout = threading.Event()

        def slow_probe():
            probe_started.set()
            if not release_probe.wait(3):
                probe_timeout.set()
            return {"gpu_name": "Test GPU"}

        cfg = {**self.win.cfg, "power_limit": 140}
        with patch.object(guard, "detect_gpu", side_effect=slow_probe), patch.object(
            guard, "query_power_range", return_value=(30, 120)
        ):
            dialog = guard.SettingsDialog(cfg, self.win)
            try:
                self.assertTrue(probe_started.wait(1))
                guard.QTimer.singleShot(0, release_probe.set)
                guard.QTimer.singleShot(250, dialog.reject)
                dialog.exec()
                self.assertFalse(probe_timeout.is_set())
                self.assertEqual(dialog.gpu_label.text(), "Test GPU")
                self.assertEqual(dialog.power_range, (30, 120))
                self.assertEqual(dialog.sp_power.value(), 140)
            finally:
                release_probe.set()
                dialog.deleteLater()

    def test_first_run_requires_account_and_owner_confirmation(self):
        window = self.win
        window.cfg["account"] = ""
        window.cfg["mining_authorized"] = False
        window._auto_boot()
        self.spawn.assert_not_called()
        window.cfg["account"] = "krxMyAccount"
        with patch.object(guard.QMessageBox, "question", return_value=guard.QMessageBox.StandardButton.No):
            window.btn_run.click()
            self.spawn.assert_not_called()
            self.assertFalse(window.btn_run.isChecked())
        with patch.object(guard.QMessageBox, "question", return_value=guard.QMessageBox.StandardButton.Yes):
            window.btn_run.click()
        self.assertTrue(window.want_run)
        self.assertTrue(json.loads(self.cfg_file.read_text(encoding="utf-8"))["mining_authorized"])

    def test_hide_blocks_on_hotkey_failure_then_restores_on_native_message(self):
        window = self.win
        window.show()
        window.cfg["global_hotkey"] = False
        window._apply_hotkey()
        window._start_miner()
        self.assertTrue(window.tray.isVisible())
        with patch.object(window.hotkey, "set_enabled", return_value=False), patch.object(guard.QMessageBox, "warning"):
            window.btn_background.click()
        self.assertTrue(window.isVisible())
        self.assertTrue(window.tray.isVisible())
        with patch.object(window.hotkey, "set_enabled", return_value=True):
            window.btn_background.click()
            self.assertFalse(window.isVisible())
            self.assertFalse(window.tray.isVisible())
            self.assertTrue(window.timer.isActive())
            self.assertTrue(window.update_timer.isActive())
            with patch.object(window, "_schedule_due", side_effect=[False, True]):
                window.cfg["sched_enable"] = True
                window._tick()
                self.assertFalse(window.want_run)
            msg = wintypes.MSG()
            msg.message = 0x0312
            msg.wParam = window.hotkey.HOTKEY_ID
            window.hotkey.registered = True
            try:
                self.assertEqual(window.hotkey.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg)), (True, 0))
                self.qt.processEvents()
            finally:
                window.hotkey.registered = False
            self.assertTrue(window.isVisible())
            self.assertTrue(window.tray.isVisible())
            window.hotkey.set_enabled.assert_called_with(False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
