"""Движок умного исправления: llama.cpp отдельным процессом.

Устроен как движок распознавания (whisper_server.py): свой процесс, который
закрывается целиком вместе с памятью модели; сбой драйвера видеокарты роняет
только его; общение — HTTP на 127.0.0.1.

Прогретый кэш промпта на Mac собирался руками. Здесь его ведёт сам движок:
инструкция и примеры одинаковы от запроса к запросу, и движок считает
заново только новый хвост — текст диктовки.

Чтобы до движка не достучались сайты из браузера, у каждого запуска свой
случайный ключ, без него запросы не принимаются.
"""

import json
import logging
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .. import winproc
from .whisper_server import (
    _VK_DEVICE_RE, EngineError, _free_port, _local_opener, default_threads,
)

log = logging.getLogger("localflow")

# Инструкция с примерами и выученными правками — около двух тысяч токенов,
# кусок диктовки режется до 420 символов. Больше контекст — больше памяти
# (у 4B и 8B это ~150 КБ на токен).
CTX_TOKENS = 4096

_OFFLOAD_RE = re.compile(r"offloaded\s+(\d+)\s*/\s*(\d+)\s+layers\s+to\s+GPU", re.IGNORECASE)
_THINK_RE = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)


class LlamaServer:
    def __init__(self, exe: Path, model: Path, log_path: Path,
                 threads: int | None = None, use_gpu: bool = True):
        self.exe = Path(exe)
        self.model = Path(model)
        self.log_path = Path(log_path)
        self.threads = threads or default_threads()
        self.use_gpu = use_gpu
        self.port = 0
        self.device = ""          # чем считает: имя видеокарты или «CPU»
        self._key = secrets.token_urlsafe(24)
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
        if not self.use_gpu:
            return "CPU"
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        m = _OFFLOAD_RE.search(text)
        if not m or m.group(1) == "0":
            return "CPU"
        gpu = _VK_DEVICE_RE.search(text)
        name = gpu.group(1).strip() if gpu else "GPU"
        return name if m.group(1) == m.group(2) else f"{name} ({m.group(1)}/{m.group(2)})"

    def refresh_device(self) -> str:
        if self.running:
            self.device = self._detect_device() or self.device
        return self.device

    def start(self, timeout: float = 300.0) -> None:
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
            "--api-key", self._key,
            "-t", str(self.threads),
            "-c", str(CTX_TOKENS),
            "-np", "1",                 # один запрос за раз — диктовка одна
            "--cache-ram", "512",       # запасные кэши промпта: правка, перевод
            "--reasoning", "off",       # «размышления» Qwen3 — секунды впустую
            "--no-webui",
        ]
        if not self.use_gpu:
            args += ["--device", "none"]
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        logf = open(self.log_path, "w", encoding="utf-8", errors="replace")
        log.info("Запускаю движок исправления: модель %s, потоков %d, %s",
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
                    f"Движок исправления завершился при запуске (код {code}):\n{self._log_tail()}")
            try:
                # 503 «Loading model», пока модель грузится; 200 — готов
                with _local_opener.open(
                        f"http://127.0.0.1:{self.port}/health", timeout=2) as r:
                    if json.loads(r.read() or b"{}").get("status") == "ok":
                        self.device = self._detect_device()
                        log.info("Движок исправления готов за %.1f c, считает: %s",
                                 time.monotonic() - t0, self.device)
                        return
            except (urllib.error.URLError, OSError, ValueError):
                pass
            time.sleep(0.2)
        self.stop()
        raise EngineError(f"Движок исправления не поднялся за {timeout:.0f} c:\n{self._log_tail()}")

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        log.info("Движок исправления остановлен, память освобождена")

    def chat(self, messages: list[dict], max_tokens: int, timeout: float | None = None) -> str:
        """Один ответ модели. temperature 0 — правка должна быть
        предсказуемой, а не творческой."""
        if not self.running:
            raise EngineError("Движок исправления не запущен")
        body = json.dumps({
            "messages": messages,
            "max_tokens": int(max_tokens),
            "temperature": 0.0,
            "cache_prompt": True,
            "chat_template_kwargs": {"enable_thinking": False},
        }, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self._key}"},
            method="POST")
        timeout = timeout or 120.0 + max_tokens
        with self._lock:
            try:
                with _local_opener.open(req, timeout=timeout) as r:
                    data = json.loads(r.read().decode("utf-8", errors="replace"))
            except urllib.error.HTTPError as exc:
                raise EngineError(
                    f"Движок исправления ответил ошибкой {exc.code}: "
                    f"{exc.read()[:300].decode('utf-8', 'replace')}") from exc
            except (urllib.error.URLError, OSError) as exc:
                alive = self.running
                raise EngineError(
                    f"Нет ответа от движка исправления ({'работает' if alive else 'упал'}): {exc}"
                    + ("" if alive else "\n" + self._log_tail())) from exc
        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise EngineError(f"Странный ответ движка исправления: {str(data)[:200]}") from exc
        return _THINK_RE.sub("", content)
