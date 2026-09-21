"""Обновление — только по кнопке в окне программы.

Проверка — один запрос к GitHub: какая версия выложена последней. Ничего о
человеке и компьютере не отправляется, сами по себе проверки не запускаются.

Обновление: установщик новой версии скачивается в папку программы, сверяется
по контрольной сумме из того же выпуска и запускается тихо. Он сам закрывает
LocalFlow, ставит новую версию поверх и запускает её снова.
"""

import json
import logging
import os
import re
import subprocess
import threading
import urllib.request
from pathlib import Path

from . import __version__
from .engine.catalog import ModelFile
from .engine.download import download

log = logging.getLogger("localflow")

REPO = "1nebog/localflow-windows"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE_URL = f"https://github.com/{REPO}"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
_TRUSTED = f"https://github.com/{REPO}/releases/"


def _feed() -> tuple[str, str]:
    """Откуда брать выпуски: GitHub, а в проверке установщика — выпуск-макет
    на этом же компьютере (только 127.0.0.1)."""
    local = os.environ.get("LOCALFLOW_UPDATE_FEED", "")
    if re.fullmatch(r"http://127\.0\.0\.1:\d+/", local):
        return local + "latest.json", local
    return API_URL, _TRUSTED


def _parse(version: str) -> tuple[int, ...] | None:
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", str(version).strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def is_newer(latest: str, current: str = __version__) -> bool:
    a, b = _parse(latest), _parse(current)
    if a is None or b is None:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def _fetch(timeout: float) -> dict:
    req = urllib.request.Request(_feed()[0], headers={
        "User-Agent": "LocalFlow", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read(1_000_000))


def check(fetch=_fetch, timeout: float = 10.0) -> dict:
    """{"state": "latest"|"available"|"error", "current", "latest", "url"}"""
    out = {"state": "error", "current": __version__, "latest": "", "url": RELEASES_URL}
    try:
        data = fetch(timeout)
        tag = str(data.get("tag_name", ""))
        if _parse(tag) is None:
            raise ValueError(f"непонятная версия {tag!r}")
        out["latest"] = tag.lstrip("v")
        if not is_newer(tag):
            out["state"] = "latest"
            return out
        out["state"] = "available"
        assets = {str(a.get("browser_download_url", "")): a for a in data.get("assets") or []}
        for url, asset in assets.items():
            if url.startswith(_feed()[1]) and url.lower().endswith(".exe"):
                out["url"] = url
                out["size"] = int(asset.get("size") or 0)
                if url + ".sha256" in assets:
                    out["sha_url"] = url + ".sha256"
                break
        log.info("Обновления: есть версия %s (у нас %s)", out["latest"], __version__)
    except Exception as exc:
        log.warning("Обновления: не удалось проверить: %s", exc)
    return out


def safe_url(url: str) -> str:
    """Открываем только страницы выпусков этой программы."""
    return url if isinstance(url, str) and url.startswith(_feed()[1]) else RELEASES_URL


MAX_INSTALLER = 300_000_000   # больше установщик быть не может — значит, что-то не то
_SHA_RE = re.compile(r"\b([0-9a-fA-F]{64})\b")


def _fetch_text(url: str, timeout: float = 20.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "LocalFlow"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(4096).decode("utf-8", "replace")


def _launch(installer: Path, args: list[str]) -> None:
    flags = 0x00000008 | 0x00000200     # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([str(installer), *args], close_fds=True,
                     creationflags=flags if os.name == "nt" else 0)


class Updater:
    """Скачать и поставить новую версию. Состояние читает окно настроек."""

    def __init__(self, folder: Path, *, log_dir: Path, autostart=lambda: True,
                 before_install=None, downloader=download, fetch_text=_fetch_text,
                 launch=_launch):
        self.folder = Path(folder)
        self.log_dir = Path(log_dir)
        self._autostart = autostart
        self._before_install = before_install
        self._download = downloader
        self._fetch_text = fetch_text
        self._launch = launch
        self.state = "idle"          # idle | downloading | installing | error
        self.progress = None         # (скачано, всего)
        self.version = ""
        self._lock = threading.Lock()

    def status(self) -> dict:
        have, total = self.progress or (0, 0)
        return {"state": self.state, "version": self.version,
                "percent": int(have * 100 / total) if total else 0}

    def start(self, info: dict) -> bool:
        url, sha_url = str(info.get("url", "")), str(info.get("sha_url", ""))
        size = int(info.get("size") or 0)
        if not (url.startswith(_feed()[1]) and url.lower().endswith(".exe")
                and sha_url == url + ".sha256" and 0 < size <= MAX_INSTALLER):
            log.warning("Обновление: неподходящий выпуск %r", info)
            return False
        with self._lock:
            if self.state in ("downloading", "installing"):
                return True
            self.state, self.progress = "downloading", (0, size)
            self.version = str(info.get("latest", ""))
        threading.Thread(target=self._run, args=(url, sha_url, size), daemon=True,
                         name="updater").start()
        return True

    def _run(self, url: str, sha_url: str, size: int) -> None:
        try:
            m = _SHA_RE.search(self._fetch_text(sha_url))
            if not m:
                raise ValueError("нет контрольной суммы")
            base, name = url.rsplit("/", 1)
            installer = self._download(
                ModelFile(name, size, m.group(1).lower(), base=base + "/"), self.folder,
                progress=lambda have, total: setattr(self, "progress", (have, total)))
            self.state = "installing"
            tasks = "autostart" if self._autostart() else "!autostart"
            if self._before_install:
                self._before_install()
            log.info("Обновление: ставлю %s", name)
            self._launch(installer, [
                "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RELAUNCH=1",
                f"/MERGETASKS={tasks}", f"/LOG={self.log_dir / 'update-setup.log'}"])
        except Exception as exc:
            log.warning("Обновление не удалось: %s", exc)
            self.state = "error"


def clean_downloads(folder: Path) -> None:
    """Установщик своё отработал — место на диске возвращаем."""
    try:
        for f in Path(folder).glob("LocalFlow-Setup-*"):
            f.unlink()
    except OSError as exc:
        log.info("Старый установщик пока не удалить: %s", exc)
