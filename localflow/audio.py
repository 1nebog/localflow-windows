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


# Старые названия звуковых устройств Windows (MME) обрезаны до 31 буквы
MME_NAME_LIMIT = 31


def _inputs(sd) -> tuple[list[tuple[int, dict]], int | None]:
    """Микрофоны одной звуковой подсистемы — той, где системный микрофон
    (обычно MME): иначе каждый микрофон виден трижды-четырежды."""
    devices = list(enumerate(sd.query_devices()))
    inputs = [(i, d) for i, d in devices if d["max_input_channels"] > 0]
    default = sd.default.device[0]
    if default is None or default < 0:
        default = sd.query_hostapis(sd.default.hostapi)["default_input_device"]
    if default is None or default < 0 or default >= len(devices):
        return inputs, None
    api = devices[default][1]["hostapi"]
    return [(i, d) for i, d in inputs if d["hostapi"] == api], default


def _full_name(name: str, others: list[str]) -> str:
    if len(name) < MME_NAME_LIMIT:
        return name
    longer = [o for o in others if o.startswith(name) and len(o) > len(name)]
    return max(longer, key=len) if longer else name


def list_input_devices() -> list[dict]:
    """Микрофоны для настроек: [{"name", "label"}]. name — чем открывать,
    label — полное название для человека."""
    try:
        import sounddevice as sd
        primary, default = _inputs(sd)
        all_names = [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]
        seen, out = set(), []
        for i, dev in primary:
            name = dev["name"]
            if name in seen:
                continue
            # «Переназначение звуковых устройств» (Sound Mapper) — это и есть
            # «как в системе», в других подсистемах его нет
            if i == default and all_names.count(name) == 1 and len(sd.query_hostapis()) > 1:
                continue
            seen.add(name)
            out.append({"name": name, "label": _full_name(name, all_names)})
        return out
    except Exception as exc:
        log.warning("Список микрофонов недоступен: %s", exc)
        return []


def find_input_device(name: str | None) -> int | None:
    """Номер микрофона по названию из настроек; None — нет такого сейчас."""
    if not name:
        return None
    try:
        import sounddevice as sd
        primary, _ = _inputs(sd)
        for i, dev in primary:
            if dev["name"] == name:
                return i
        for i, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0 and dev["name"] == name:
                return i
    except Exception as exc:
        log.warning("Микрофон %r не найден: %s", name, exc)
    return None


# Пик всей записи ниже этого — звук до программы не дошёл: даже тихий
# микрофон в тишине шумит громче. Бывает, когда один способ подключения
# к звуку (звуковая подсистема Windows) отдаёт пустоту, а другой — голос.
DEAD_PEAK = 0.001
# Куда пробовать уйти, если микрофон молчит: от самой современной подсистемы
FALLBACK_APIS = ("Windows WASAPI", "Windows DirectSound")


def _api_name(sd, index: int) -> str:
    return sd.query_hostapis(index)["name"]


def alternative_device(sd, device: int | None, avoid: set[str]) -> int | None:
    """Тот же микрофон в другой звуковой подсистеме. device=None — системный."""
    devices = sd.query_devices()
    primary, default = _inputs(sd)
    cur = default if device is None else device
    name = devices[cur]["name"] if cur is not None else None
    system = device is None or cur == default
    for api_name in FALLBACK_APIS:
        if api_name in avoid:
            continue
        api = next((i for i, a in enumerate(sd.query_hostapis()) if a["name"] == api_name), None)
        if api is None:
            continue
        if system:
            idx = sd.query_hostapis(api)["default_input_device"]
            if idx is not None and 0 <= idx < len(devices):
                return idx
            continue
        for i, d in enumerate(devices):
            # в старой подсистеме название обрезано до 31 буквы
            if (d["hostapi"] == api and d["max_input_channels"] > 0
                    and (d["name"] == name or (len(name) >= MME_NAME_LIMIT and d["name"].startswith(name)))):
                return i
    return None


def refresh_devices() -> None:
    """Звуковая библиотека запоминает устройства при запуске; чтобы увидеть
    подключённый потом микрофон, её надо перезапустить. Только когда ни
    один микрофон не открыт."""
    try:
        import sounddevice as sd
        sd._terminate()
        sd._initialize()
    except Exception as exc:
        log.warning("Список устройств не обновился: %s", exc)


class AudioRecorder:
    """Пишет микрофон в память через callback."""

    QUIET_PEAK = 0.05        # громче — микрофон нормальный, не трогаем
    GAIN_TARGET_PEAK = 0.25  # к такому пику тянем тихий голос
    GAIN_MAX = 12.0
    GAIN_NOISE_RMS = 0.004   # фон после усиления не громче этого
    PEAK_HALF_LIFE = 1.0     # секунд, за которые пик «забывается» вдвое
    GAIN_DOUBLE_SEC = 0.2    # усиление растёт вдвое не быстрее, чем за столько

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
        self.gain = 1.0   # усиление тихого микрофона, помним между записями
        self._peak_hold = 0.0
        self._noise = None
        self._gain_logged = False
        # Прогрев и настоящий старт не должны открывать микрофон одновременно
        self._open_lock = threading.Lock()
        self.raw_peak = 0.0            # пик записи до усиления
        self.avoid_apis: set[str] = set()   # подсистемы, где микрофон молчал
        self.on_avoid_change = None    # колбэк: сохранить avoid_apis
        self._api = ""                 # через какую подсистему открыт микрофон

    @property
    def is_recording(self) -> bool:
        return self._recording

    def _amplify(self, mono: np.ndarray) -> np.ndarray:
        """Тихий микрофон (частая история у ноутбуков) подтягиваем до обычной
        громкости: иначе голос не отличить от тишины и ничего не распознаётся.
        Фон при этом не раздуваем — на шипении Whisper выдумывает слова."""
        if not len(mono):
            return mono
        peak = float(np.max(np.abs(mono)))
        rms = float(np.sqrt(np.mean(mono ** 2)))
        if self._recording and peak > self.raw_peak:
            self.raw_peak = peak
        sec = len(mono) / self._rate
        self._peak_hold = max(peak, self._peak_hold * 0.5 ** (sec / self.PEAK_HALF_LIFE))
        self._noise = rms if self._noise is None or rms < self._noise else (
            self._noise + (rms - self._noise) * 0.003)
        if self._peak_hold >= self.QUIET_PEAK or self._peak_hold < DEAD_PEAK:
            want = 1.0      # громкий — не нужно; пустоту усиливать бесполезно
        else:
            want = min(self.GAIN_MAX, self.GAIN_TARGET_PEAK / max(self._peak_hold, 1e-6),
                       self.GAIN_NOISE_RMS / max(self._noise, 1e-6))
            want = max(1.0, want)
        # вниз — сразу (не перегрузить), вверх — плавно
        self.gain = want if want < self.gain else min(want, self.gain * 2 ** (sec / self.GAIN_DOUBLE_SEC))
        if self.gain > 1.0 and not self._gain_logged:
            self._gain_logged = True
            log.info("Тихий микрофон (пик %.4f, фон %.4f) — усиливаю", peak, rms)
        if self.gain == 1.0:
            return mono
        return np.clip(mono * self.gain, -1.0, 1.0)

    def _callback(self, indata, frames, time_info, status):
        if status:
            log.warning("Аудио-статус: %s", status)
        mono = indata if indata.ndim == 1 or indata.shape[1] == 1 else indata.mean(axis=1, keepdims=True)
        mono = self._amplify(mono)
        self.level = float(np.sqrt(np.mean(mono ** 2)))
        with self._lock:
            if self._recording:
                self._chunks.append(mono.copy())

    def input_devices(self) -> list[dict]:
        """Свежий список микрофонов. Во время записи — без перезапуска
        звуковой библиотеки (прежний список)."""
        if not self._open_lock.acquire(timeout=3):
            return list_input_devices()
        try:
            if not self._recording and self._stream is None:
                refresh_devices()
            return list_input_devices()
        finally:
            self._open_lock.release()

    def _open(self, callback=None, system: bool = False):
        """Открыть микрофон: сначала как ждёт Whisper, потом как умеет он сам."""
        device = None if system else find_input_device(self.device)
        if self.device and device is None and not system:
            # выбранный микрофон отключили — пишем системным, выбор помним:
            # подключат обратно — снова возьмём его
            log.warning("Микрофон %r не подключён — беру системный", self.device)
        tries = [(SAMPLE_RATE, CHANNELS)]
        api = ""
        try:
            import sounddevice as sd
            if self.avoid_apis:
                info = sd.query_devices(device, "input")
                if _api_name(sd, info["hostapi"]) in self.avoid_apis:
                    alt = alternative_device(sd, device, self.avoid_apis)
                    if alt is not None:
                        device = alt
            info = sd.query_devices(device, "input")
            api = _api_name(sd, info["hostapi"])
            native = int(info["default_samplerate"])
            tries += [(native, CHANNELS), (native, max(1, min(2, info["max_input_channels"])))]
        except Exception:
            pass
        last = None
        for rate, ch in dict.fromkeys(tries):
            try:
                stream = self._factory(samplerate=rate, channels=ch, dtype="float32",
                                       device=device, callback=callback)
                stream.start()
                if (rate, ch) != (SAMPLE_RATE, CHANNELS):
                    log.info("Микрофон пишет %d Гц, %d кан. — переведу в 16 кГц", rate, ch)
                self._rate, self._channels, self._api = rate, ch, api
                return stream
            except Exception as exc:
                last = exc
        # Выбранный микрофон не открылся (занят, сломан драйвер) — системный
        if device is not None:
            log.warning("Микрофон %r не открылся (%s) — беру системный", self.device, last)
            return self._open(callback, system=True)
        raise last

    def switch_api(self) -> bool:
        """Микрофон молчал — в следующий раз открыть его через другую
        подсистему. False — пробовать больше негде."""
        if not self._api:
            return False
        try:
            import sounddevice as sd
            device = find_input_device(self.device)
            avoid = self.avoid_apis | {self._api}
            if alternative_device(sd, device, avoid) is None:
                return False
        except Exception as exc:
            log.warning("Другой способ открыть микрофон не нашёлся: %s", exc)
            return False
        log.warning("Микрофон молчит через %s — попробую другую подсистему", self._api)
        self.avoid_apis = avoid
        if self.on_avoid_change:
            self.on_avoid_change(sorted(avoid))
        return True

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
            self._gain_logged = False
            self.raw_peak = 0.0
        try:
            with self._open_lock:
                self._stream = self._open(self._callback)
        except Exception:
            with self._lock:
                self._recording = False
            raise
        self._started_at = time.monotonic()
        log.info("Запись началась (микрофон открылся за %.2f c, %s, %d Гц)",
                 self._started_at - t_open, self._api or "?", self._rate)

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
        log.info("Запись остановлена, длительность ~%.2f c, пик %.5f, усиление ×%.1f",
                 time.monotonic() - self._started_at, self.raw_peak, self.gain)
        return self._join(chunks)
