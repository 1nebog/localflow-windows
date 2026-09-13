"""Движок распознавания: whisper.cpp отдельным процессом.

Почему отдельный процесс, а не библиотека внутри программы:
- модель выгружается целиком одним движением — закрыли процесс, и память
  вернулась системе (на Mac это «выгрузка при простое»);
- сбой драйвера видеокарты роняет движок, а не всю программу: LocalFlow
  перезапустит его, при необходимости уже на процессоре;
- одна сборка работает на любом железе. Рядом с движком лежат варианты под
  разные процессоры и модуль для видеокарт (Vulkan), движок сам берёт лучший
  из того, что есть на компьютере.

Общение — обычные HTTP-запросы на 127.0.0.1, наружу ничего не уходит.
"""

import json
import logging
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .. import winproc

log = logging.getLogger("localflow")

# Запросы к своему же движку не должны идти через системный прокси
_local_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class EngineError(Exception):
    pass


def default_threads() -> int:
    """Потоков для расчёта: по числу физических ядер, но в разумных рамках.
    Логических ядер при гиперпоточности вдвое больше физических, а лишние
    потоки движку только мешают."""
    n = os.cpu_count() or 4
    physical = n // 2 if n >= 8 else n
    return max(2, min(8, physical))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _multipart(fields: dict, audio: bytes) -> tuple[bytes, str]:
    boundary = "localflow" + uuid.uuid4().hex
    out = []
    for key, value in fields.items():
        if isinstance(value, bool):
            value = "true" if value else "false"
        out.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"'
            f"\r\n\r\n".encode() + str(value).encode("utf-8") + b"\r\n")
    out.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode()
        + audio + b"\r\n")
    out.append(f"--{boundary}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


# Что движок пишет при старте о видеокарте
_VK_DEVICE_RE = re.compile(r"ggml_vulkan:\s*\d+\s*=\s*([^|\r\n]+?)\s*(?:\(|\|)")
_GPU_BACKEND_RE = re.compile(r"using\s+(\w+)\s+backend", re.IGNORECASE)


class WhisperServer:
    def __init__(self, exe: Path, model: Path, log_path: Path,
                 threads: int | None = None, use_gpu: bool = True,
                 no_speech_thold: float = 0.85):
        self.exe = Path(exe)
        self.model = Path(model)
        self.log_path = Path(log_path)
        self.threads = threads or default_threads()
        self.use_gpu = use_gpu
        self.no_speech_thold = no_speech_thold
        self.port = 0
        self.device = ""          # чем считает: имя видеокарты или «CPU»
        self._proc = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _log_tail(self, n: int = 25) -> str:
        try:
            lines = self.log_path.read_text(encoding="utf-8", errors="replace")
            return "\n".join(lines.splitlines()[-n:])
        except OSError:
            return ""

    def _detect_device(self) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        if self.use_gpu:
            m = _VK_DEVICE_RE.search(text)
            if m and "vulkan" in text.lower() and not re.search(
                    r"no GPU found|failed to initialize", text, re.IGNORECASE):
                return m.group(1).strip()
            m = _GPU_BACKEND_RE.search(text)
            if m and m.group(1).lower() != "cpu":
                return m.group(1)
        return "CPU"

    def start(self, timeout: float = 180.0) -> None:
        if self.running:
            return
        if not self.exe.is_file():
            raise EngineError(f"Нет движка: {self.exe}")
        if not self.model.is_file():
            raise EngineError(f"Нет модели: {self.model}")
        self.port = _free_port()
        args = [
            str(self.exe), "-m", str(self.model),
            "--host", "127.0.0.1", "--port", str(self.port),
            "-t", str(self.threads),
            "-nth", str(self.no_speech_thold),
        ]
        if not self.use_gpu:
            args.append("-ng")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        logf = open(self.log_path, "w", encoding="utf-8", errors="replace")
        log.info("Запускаю движок распознавания: модель %s, потоков %d, %s",
                 self.model.name, self.threads,
                 "видеокарта разрешена" if self.use_gpu else "только процессор")
        t0 = time.monotonic()
        try:
            self._proc = winproc.spawn(args, cwd=str(self.exe.parent),
                                       stdout=logf, stderr=logf)
        finally:
            logf.close()  # дескриптор унаследован процессом

        while time.monotonic() - t0 < timeout:
            if self._proc.poll() is not None:
                code = self._proc.returncode
                self._proc = None
                raise EngineError(
                    f"Движок завершился при запуске (код {code}):\n{self._log_tail()}")
            try:
                with _local_opener.open(
                        f"http://127.0.0.1:{self.port}/health", timeout=2) as r:
                    if json.loads(r.read() or b"{}").get("status") == "ok":
                        self.device = self._detect_device()
                        log.info("Движок готов за %.1f c, считает: %s",
                                 time.monotonic() - t0, self.device)
                        return
            except (urllib.error.URLError, OSError, ValueError):
                pass
            time.sleep(0.15)
        self.stop()
        raise EngineError(f"Движок не поднялся за {timeout:.0f} c:\n{self._log_tail()}")

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        log.info("Движок распознавания остановлен, память освобождена")

    def inference(self, wav: bytes, fields: dict, timeout: float = 300.0) -> dict:
        """Один запрос распознавания. fields — поля формы whisper-server."""
        if not self.running:
            raise EngineError("Движок не запущен")
        body, ctype = _multipart(fields, wav)
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/inference", data=body,
            headers={"Content-Type": ctype}, method="POST")
        with self._lock:
            try:
                with _local_opener.open(req, timeout=timeout) as r:
                    data = json.loads(r.read().decode("utf-8", errors="replace"))
            except urllib.error.HTTPError as exc:
                raise EngineError(
                    f"Движок ответил ошибкой {exc.code}: "
                    f"{exc.read()[:300].decode('utf-8', 'replace')}") from exc
            except (urllib.error.URLError, OSError) as exc:
                alive = self.running
                raise EngineError(
                    f"Нет ответа от движка ({'работает' if alive else 'упал'}): {exc}"
                    + ("" if alive else "\n" + self._log_tail())) from exc
        if "error" in data:
            raise EngineError(f"Движок: {data['error']}")
        return data
