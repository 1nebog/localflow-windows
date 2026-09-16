"""Проверка обновлений — только по кнопке в окне программы.

Один запрос к GitHub: какая версия выложена последней. Ничего о человеке
и компьютере не отправляется, сами по себе проверки не запускаются.
"""

import json
import logging
import re
import urllib.request

from . import __version__

log = logging.getLogger("localflow")

REPO = "1nebog/localflow-windows"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
_TRUSTED = f"https://github.com/{REPO}/releases/"


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
    req = urllib.request.Request(API_URL, headers={
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
        for asset in data.get("assets") or []:
            url = str(asset.get("browser_download_url", ""))
            if url.startswith(_TRUSTED) and url.lower().endswith(".exe"):
                out["url"] = url
                break
        log.info("Обновления: есть версия %s (у нас %s)", out["latest"], __version__)
    except Exception as exc:
        log.warning("Обновления: не удалось проверить: %s", exc)
    return out


def safe_url(url: str) -> str:
    """Открываем только страницы выпусков этой программы."""
    return url if isinstance(url, str) and url.startswith(_TRUSTED) else RELEASES_URL
