"""Скачивание моделей: с докачкой, проверкой и без полуфабрикатов на диске.

Файл качается в «имя.part». Оборвался интернет — следующая попытка
продолжит с того же места. Готовый файл сверяется по контрольной сумме и
только потом получает настоящее имя, поэтому программа никогда не
возьмёт недокачанную или битую модель.
"""

import hashlib
import logging
import urllib.request
from pathlib import Path
from typing import Callable

from .catalog import ModelFile

log = logging.getLogger("localflow")

CHUNK = 1 << 20  # 1 МБ


class DownloadError(Exception):
    pass


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def is_ready(model: ModelFile, folder: Path) -> bool:
    """Модель на месте и нужного размера. Сумму здесь не считаем: на
    медленном диске это секунды на каждый запуск, её проверяет download()."""
    path = folder / model.file
    return path.is_file() and path.stat().st_size == model.size


def download(model: ModelFile, folder: Path,
             progress: Callable[[int, int], None] | None = None,
             cancelled: Callable[[], bool] | None = None,
             timeout: float = 30.0) -> Path:
    """Скачать модель в folder и вернуть путь к готовому файлу."""
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / model.file
    if is_ready(model, folder):
        return final
    part = folder / (model.file + ".part")
    have = part.stat().st_size if part.exists() else 0
    if have > model.size:
        part.unlink()
        have = 0

    if have < model.size:
        req = urllib.request.Request(model.url, headers={
            "User-Agent": "LocalFlow",
            **({"Range": f"bytes={have}-"} if have else {}),
        })
        log.info("Качаю модель %s (%d МБ), уже есть %d МБ",
                 model.file, model.size_mb, have // 1_000_000)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if have and resp.status != 206:
                    have = 0  # сервер не умеет докачку — начинаем заново
                with open(part, "ab" if have else "wb") as out:
                    while True:
                        if cancelled and cancelled():
                            raise DownloadError("Скачивание отменено")
                        block = resp.read(CHUNK)
                        if not block:
                            break
                        out.write(block)
                        have += len(block)
                        if progress:
                            progress(have, model.size)
        except DownloadError:
            raise
        except Exception as exc:
            raise DownloadError(f"Не удалось скачать {model.file}: {exc}") from exc

    if part.stat().st_size != model.size:
        raise DownloadError(
            f"{model.file}: скачано {part.stat().st_size} байт из {model.size}")
    if sha256_of(part) != model.sha256:
        part.unlink()
        raise DownloadError(f"{model.file}: файл повреждён, скачаю заново")
    part.replace(final)
    log.info("Модель %s готова", model.file)
    return final
