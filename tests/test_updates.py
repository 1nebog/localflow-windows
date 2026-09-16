"""Проверка обновлений без интернета."""

import hashlib
import time

from localflow import __version__, updates
from localflow.engine.download import DownloadError


EXE = "https://github.com/1nebog/localflow-windows/releases/download/v9.0.0/LocalFlow-Setup-9.0.0.exe"


def release(tag, assets=()):
    return lambda timeout: {"tag_name": tag, "assets": [
        {"browser_download_url": u, "size": 1000} for u in assets]}


def test_versions_compare_as_numbers():
    assert updates.is_newer("v0.10.0", "0.9.9")
    assert updates.is_newer("0.1.1", "0.1")
    assert not updates.is_newer("v0.1.0", "0.1.0")
    assert not updates.is_newer("v0.0.9", "0.1.0")
    assert not updates.is_newer("nightly", "0.1.0")


def test_latest_already_installed():
    r = updates.check(release("v" + __version__))
    assert r["state"] == "latest" and r["latest"] == __version__


def test_new_version_links_straight_to_installer():
    r = updates.check(release("v9.0.0", [EXE + ".sha256", EXE]))
    assert r == {"state": "available", "current": __version__, "latest": "9.0.0", "url": EXE,
                 "size": 1000, "sha_url": EXE + ".sha256"}


def test_foreign_download_link_is_ignored():
    r = updates.check(release("v9.0.0", ["https://evil.example/LocalFlow-Setup.exe"]))
    assert r["state"] == "available" and r["url"] == updates.RELEASES_URL
    assert updates.safe_url("https://evil.example/x.exe") == updates.RELEASES_URL


def test_no_internet_is_not_a_crash():
    def offline(timeout):
        raise OSError("нет сети")
    assert updates.check(offline)["state"] == "error"


def wait(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def make_updater(tmp_path, body=b"installer", sha=None, autostart=True):
    launched, events = [], []

    def downloader(model, folder, progress=None, cancelled=None):
        progress(len(body) // 2, model.size)
        if hashlib.sha256(body).hexdigest() != model.sha256:
            raise DownloadError("файл повреждён")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / model.file).write_bytes(body)
        events.append(("download", model.url, model.size))
        return folder / model.file

    u = updates.Updater(
        tmp_path / "updates", log_dir=tmp_path / "logs", autostart=lambda: autostart,
        before_install=lambda: events.append(("before",)),
        downloader=downloader,
        fetch_text=lambda url: f"{sha or hashlib.sha256(body).hexdigest()}  LocalFlow-Setup-9.0.0.exe\n",
        launch=lambda path, args: launched.append((path, args)))
    return u, launched, events


INFO = {"state": "available", "latest": "9.0.0", "url": EXE, "size": 9, "sha_url": EXE + ".sha256"}


def test_update_downloads_checks_and_runs_installer_silently(tmp_path):
    u, launched, events = make_updater(tmp_path)
    assert u.start(INFO)
    assert wait(lambda: launched)
    path, args = launched[0]
    assert path.name == "LocalFlow-Setup-9.0.0.exe" and path.read_bytes() == b"installer"
    assert events == [("download", EXE, 9), ("before",)]
    assert "/VERYSILENT" in args and "/RELAUNCH=1" in args and "/MERGETASKS=autostart" in args
    assert u.status() == {"state": "installing", "version": "9.0.0", "percent": 44}


def test_update_keeps_autostart_off(tmp_path):
    u, launched, _ = make_updater(tmp_path, autostart=False)
    u.start(INFO)
    assert wait(lambda: launched)
    assert "/MERGETASKS=!autostart" in launched[0][1]


def test_broken_download_is_never_run(tmp_path):
    u, launched, _ = make_updater(tmp_path, sha="0" * 64)
    u.start(INFO)
    assert wait(lambda: u.state == "error")
    assert launched == []


def test_only_this_apps_release_can_be_installed(tmp_path):
    u, launched, _ = make_updater(tmp_path)
    assert not u.start({**INFO, "url": "https://evil.example/LocalFlow-Setup.exe"})
    assert not u.start({**INFO, "sha_url": ""})             # без суммы не ставим
    assert not u.start({**INFO, "size": 10**9})
    assert u.state == "idle" and launched == []


def test_old_installers_are_cleaned(tmp_path):
    folder = tmp_path / "updates"
    folder.mkdir()
    (folder / "LocalFlow-Setup-0.1.2.exe").write_bytes(b"x")
    updates.clean_downloads(folder)
    assert list(folder.iterdir()) == []


def test_local_feed_only_for_this_computer(monkeypatch):
    monkeypatch.setenv("LOCALFLOW_UPDATE_FEED", "http://127.0.0.1:8123/")
    assert updates._feed() == ("http://127.0.0.1:8123/latest.json", "http://127.0.0.1:8123/")
    monkeypatch.setenv("LOCALFLOW_UPDATE_FEED", "http://evil.example:80/")
    assert updates._feed() == (updates.API_URL, updates._TRUSTED)
