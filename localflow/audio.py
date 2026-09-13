"""Запись с микрофона в память.

Перенос `AudioRecorder` с Mac. Отличие одно: не каждый микрофон на Windows
соглашается сразу отдавать 16 кГц моно, которые ждёт Whisper. Тогда пишем
так, как он умеет (обычно 48 кГц, иногда стерео), а в 16 кГц переводим уже
после записи.
"""

import logging
import threading
import time

import numpy as np

from .core import CHANNELS, SAMPLE_RATE

log = logging.getLogger("localflow")


def _lowpass(x: np.ndarray, cutoff: float, rate: float, taps: int = 101) -> np.ndarray:
    n = np.arange(taps) - (taps - 1) / 2
    fc = cutoff / rate
    h = 2 * fc * np.sinc(2 * fc * n) * np.blackman(taps)
    h /= h.sum()
    return np.convolve(x, h.astype(np.float32), mode="same")


def resample_to_16k(x: np.ndarray, rate: int) -> np.ndarray:
    """В 16 кГц. Сначала срезаем всё выше 7.6 кГц (иначе при прореживании
    высокие частоты завернутся в слышимый шум), потом берём отсчёты."""
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    if rate == SAMPLE_RATE or len(x) == 0:
        return x
    if rate > SAMPLE_RATE:
        x = _lowpass(x, 7600.0, rate)
        if rate % SAMPLE_RATE == 0:
            return np.ascontiguousarray(x[:: rate // SAMPLE_RATE])
    n_out = int(round(len(x) * SAMPLE_RATE / rate))
    t_out = np.arange(n_out, dtype=np.float64) * (rate / SAMPLE_RATE)
    return np.interp(t_out, np.arange(len(x)), x).astype(np.float32)


def _default_stream_factory(**kw):
    import sounddevice as sd
    return sd.InputStream(**kw)


def list_input_devices() -> list[dict]:
    """Микрофоны для настроек: [{"name", "default"}]."""
    try:
        import sounddevice as sd
        default = sd.default.device[0]
        seen, out = set(), []
        for i, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] <= 0 or dev["name"] in seen:
                continue
            seen.add(dev["name"])
            out.append({"name": dev["name"], "default": i == default})
        return out
    except Exception as exc:
        log.warning("Список микрофонов недоступен: %s", exc)
        return []


class AudioRecorder:
    """Пишет микрофон в память через callback."""

    def __init__(self, stream_factory=None):
        self._factory = stream_factory or _default_stream_factory
        self.device = None            # имя микрофона из настроек, None — системный
        self._stream = None
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._recording = False
        self._started_at = 0.0
        self._rate = SAMPLE_RATE      # на какой частоте реально пишем
        self._channels = CHANNELS
        self.level = 0.0  # текущая громкость (RMS) для индикатора
        # Прогрев и настоящий старт не должны открывать микрофон одновременно
        self._open_lock = threading.Lock()

    @property
    def is_recording(self) -> bool:
        return self._recording

    def _callback(self, indata, frames, time_info, status):
        if status:
            log.warning("Аудио-статус: %s", status)
        mono = indata if indata.ndim == 1 or indata.shape[1] == 1 else indata.mean(axis=1, keepdims=True)
        self.level = float(np.sqrt(np.mean(mono ** 2)))
        with self._lock:
            if self._recording:
                self._chunks.append(mono.copy())

    def _open(self, callback=None):
        """Открыть микрофон: сначала как ждёт Whisper, потом как умеет он сам."""
        tries = [(SAMPLE_RATE, CHANNELS)]
        try:
            import sounddevice as sd
            info = sd.query_devices(self.device, "input")
            native = int(info["default_samplerate"])
            tries += [(native, CHANNELS), (native, max(1, min(2, info["max_input_channels"])))]
        except Exception:
            pass
        last = None
        for rate, ch in dict.fromkeys(tries):
            try:
                stream = self._factory(samplerate=rate, channels=ch, dtype="float32",
                                       device=self.device, callback=callback)
                stream.start()
                if (rate, ch) != (SAMPLE_RATE, CHANNELS):
                    log.info("Микрофон пишет %d Гц, %d кан. — переведу в 16 кГц", rate, ch)
                self._rate, self._channels = rate, ch
                return stream
            except Exception as exc:
                last = exc
        # Выбранный в настройках микрофон отключили — берём системный
        if self.device is not None:
            log.warning("Микрофон %r недоступен (%s) — беру системный", self.device, last)
            self.device = None
            return self._open(callback)
        raise last

    def warm_up(self) -> None:
        """Открыть и сразу закрыть микрофон, чтобы звуковая система проснулась:
        первое открытие за сеанс заметно дольше следующих."""
        t0 = time.monotonic()
        try:
            with self._open_lock:
                if self._recording:
                    return
                stream = self._open()
                stream.stop()
                stream.close()
            log.info("Микрофон прогрет за %.2f c", time.monotonic() - t0)
        except Exception as exc:
            log.warning("Прогрев микрофона не удался: %s", exc)

    def start(self) -> None:
        """Начать запись. Бросает исключение, если микрофон недоступен."""
        t_open = time.monotonic()
        with self._lock:
            self._chunks = []
            self._recording = True
        try:
            with self._open_lock:
                self._stream = self._open(self._callback)
        except Exception:
            with self._lock:
                self._recording = False
            raise
        self._started_at = time.monotonic()
        log.info("Запись началась (микрофон открылся за %.2f c)",
                 self._started_at - t_open)

    def _join(self, chunks) -> np.ndarray:
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return resample_to_16k(np.concatenate(chunks).reshape(-1), self._rate)

    def tail(self, seconds: float) -> np.ndarray:
        """Последние N секунд записи, не останавливая её (16 кГц, моно)."""
        n = int(seconds * self._rate)
        with self._lock:
            picked, total = [], 0
            for ch in reversed(self._chunks):
                picked.append(ch)
                total += len(ch)
                if total >= n:
                    break
        if not picked:
            return np.zeros(0, dtype=np.float32)
        return resample_to_16k(np.concatenate(picked[::-1]).reshape(-1)[-n:], self._rate)

    def stop(self) -> np.ndarray:
        """Остановить запись и вернуть буфер (float32, 16 кГц, моно)."""
        with self._lock:
            self._recording = False
            chunks = self._chunks
            self._chunks = []
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:
                log.warning("Микрофон закрылся с ошибкой: %s", exc)
            self._stream = None
        log.info("Запись остановлена, длительность ~%.2f c",
                 time.monotonic() - self._started_at)
        return self._join(chunks)
