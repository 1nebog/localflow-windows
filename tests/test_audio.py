"""Запись: перевод частоты и буфер без настоящего микрофона."""

import numpy as np

from localflow.audio import AudioRecorder, resample_to_16k


def tone(freq, rate, sec=1.0, amp=0.5):
    t = np.arange(int(rate * sec)) / rate
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def dominant_freq(x, rate=16000):
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return np.fft.rfftfreq(len(x), 1 / rate)[spec.argmax()]


def test_48k_to_16k_keeps_speech_band():
    out = resample_to_16k(tone(1000, 48000), 48000)
    assert len(out) == 16000
    assert abs(dominant_freq(out) - 1000) < 5
    assert 0.45 < np.abs(out[1000:-1000]).max() < 0.55


def test_44k_to_16k():
    out = resample_to_16k(tone(440, 44100, sec=2.0), 44100)
    assert abs(len(out) - 32000) <= 1
    assert abs(dominant_freq(out) - 440) < 5


def test_high_frequencies_do_not_fold_into_speech():
    # 10 кГц не помещается в 16 кГц; без фильтра он превратился бы в 6 кГц
    out = resample_to_16k(tone(10000, 48000), 48000)
    assert np.abs(out[1000:-1000]).max() < 0.02


class FakeStream:
    def __init__(self, callback=None, **kw):
        self.callback = callback
        self.kw = kw
        self.started = self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        pass

    def close(self):
        self.closed = True


def test_recorder_collects_chunks_and_tail():
    streams = []

    def factory(**kw):
        s = FakeStream(**kw)
        streams.append(s)
        return s

    rec = AudioRecorder(stream_factory=factory)
    rec.start()
    assert rec.is_recording and streams[-1].kw["samplerate"] == 16000
    cb = streams[-1].callback
    for i in range(10):   # 10 кусков по 0.1 c
        cb(np.full((1600, 1), 0.1 * (i + 1), np.float32), 1600, None, None)
    assert abs(rec.level - 1.0) < 1e-6
    tail = rec.tail(0.25)
    assert len(tail) == 4000 and tail[-1] == np.float32(1.0)
    audio = rec.stop()
    assert len(audio) == 16000 and not rec.is_recording and streams[-1].closed


def speech_like(amp, sec=0.1, rate=16000, noise=0.0002, seed=0):
    rng = np.random.default_rng(seed)
    x = tone(220, rate, sec, amp) + rng.normal(0, noise, int(rate * sec)).astype(np.float32)
    return x.reshape(-1, 1)


def test_quiet_laptop_microphone_is_amplified_to_speech_level():
    rec = AudioRecorder(stream_factory=lambda **kw: FakeStream(**kw))
    rec.start()
    for i in range(20):              # 2 c тихой речи с паузами между словами
        rec._callback(speech_like(0.012 if i % 3 else 0.0, seed=i), 1600, None, None)
    assert rec.gain > 8
    audio = rec.stop()
    assert np.abs(audio[-1600:]).max() > 0.1   # теперь громче порога тишины
    rec.start()
    rec._callback(speech_like(0.012), 1600, None, None)
    assert rec.level > 0.02                    # следующая запись — сразу громко


def test_normal_microphone_is_not_touched():
    rec = AudioRecorder(stream_factory=lambda **kw: FakeStream(**kw))
    rec.start()
    for i in range(20):
        rec._callback(speech_like(0.4, seed=i), 1600, None, None)
    assert rec.gain == 1.0


def test_background_hiss_is_not_blown_up_into_speech():
    rec = AudioRecorder(stream_factory=lambda **kw: FakeStream(**kw))
    rec.start()
    rng = np.random.default_rng(1)
    for _ in range(30):                        # шум без голоса, фон 0.002
        rec._callback(rng.normal(0, 0.002, (1600, 1)).astype(np.float32), 1600, None, None)
    audio = rec.stop()
    assert np.sqrt(np.mean(audio[-8000:] ** 2)) <= 0.0045


def test_loud_word_after_quiet_start_is_not_clipped_for_long():
    rec = AudioRecorder(stream_factory=lambda **kw: FakeStream(**kw))
    rec.start()
    for i in range(10):
        rec._callback(speech_like(0.01, seed=i), 1600, None, None)
    rec._callback(speech_like(0.5), 1600, None, None)
    assert rec.gain == 1.0


def test_windows_blocked_microphone_stays_zero():
    rec = AudioRecorder(stream_factory=lambda **kw: FakeStream(**kw))
    rec.start()
    for _ in range(10):
        rec._callback(np.zeros((1600, 1), np.float32), 1600, None, None)
    assert np.max(np.abs(rec.stop())) == 0.0     # не маскируем запрет доступа


def test_stereo_is_mixed_to_mono():
    rec = AudioRecorder(stream_factory=lambda **kw: FakeStream(**kw))
    rec.start()
    stereo = np.stack([np.full(800, 0.2, np.float32), np.zeros(800, np.float32)], axis=1)
    rec._callback(stereo, 800, None, None)
    assert np.allclose(rec.stop(), 0.1)


# --- Список микрофонов как на Windows ------------------------------------------

import sys  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from localflow import audio  # noqa: E402

REALTEK = "Микрофон (Realtek(R) Audio)"
ARRAY = "Микрофонный массив (Intel® Smart Sound Technology)"


class FakeSd:
    """Одни и те же микрофоны в трёх звуковых подсистемах Windows."""

    def __init__(self):
        self.hostapis = [{"name": "MME", "default_input_device": 0},
                         {"name": "Windows DirectSound", "default_input_device": 4},
                         {"name": "Windows WASAPI", "default_input_device": 7}]
        dev = lambda name, api, inp=2: {"name": name, "hostapi": api, "max_input_channels": inp,
                                        "default_samplerate": 48000.0}
        self.devices = [
            dev("Переназначение звуковых устр. - Input", 0), dev(REALTEK, 0), dev(ARRAY[:31], 0),
            dev("Переназначение звуковых устр. - Output", 0, inp=0),
            dev("Первичный драйвер записи звука", 1), dev(REALTEK, 1), dev(ARRAY, 1),
            dev(REALTEK, 2), dev(ARRAY, 2),
        ]
        self.default = SimpleNamespace(device=[0, 3], hostapi=0)
        self.restarts = 0

    def query_devices(self, device=None, kind=None):
        if device is None and kind is None:
            return list(self.devices)
        return self.devices[self.default.device[0] if device is None else device]

    def query_hostapis(self, index=None):
        return tuple(self.hostapis) if index is None else self.hostapis[index]

    def _terminate(self):
        self.restarts += 1

    def _initialize(self):
        pass


@pytest.fixture
def fake_sd(monkeypatch):
    sd = FakeSd()
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    return sd


def test_each_microphone_listed_once_with_full_name(fake_sd):
    assert audio.list_input_devices() == [
        {"name": REALTEK, "label": REALTEK},
        {"name": ARRAY[:31], "label": ARRAY},      # обрезанное имя MME — полное из WASAPI
    ]
    assert audio.find_input_device(REALTEK) == 1
    assert audio.find_input_device(ARRAY[:31]) == 2
    assert audio.find_input_device("USB Mic") is None


def test_chosen_microphone_opens_by_number(fake_sd):
    streams = []

    def factory(**kw):
        streams.append(kw)
        return FakeStream(**kw)

    rec = AudioRecorder(stream_factory=factory)
    rec.device = REALTEK
    rec.start()
    rec.stop()
    # по названию sounddevice нашёл бы его трижды и отказался открывать
    assert streams[-1]["device"] == 1


def test_unplugged_microphone_falls_back_but_is_remembered(fake_sd):
    streams = []
    rec = AudioRecorder(stream_factory=lambda **kw: streams.append(kw) or FakeStream(**kw))
    rec.device = "USB Mic"
    rec.start()
    rec.stop()
    assert streams[-1]["device"] is None and rec.device == "USB Mic"


def test_broken_microphone_falls_back_to_system(fake_sd):
    streams = []

    def factory(**kw):
        streams.append(kw["device"])
        if kw["device"] == 1:
            raise OSError("занят другой программой")
        return FakeStream(**kw)

    rec = AudioRecorder(stream_factory=factory)
    rec.device = REALTEK
    rec.start()
    rec.stop()
    assert streams[-1] is None and rec.device == REALTEK


def test_device_list_refreshes_only_when_microphone_is_closed(fake_sd):
    rec = AudioRecorder(stream_factory=lambda **kw: FakeStream(**kw))
    assert len(rec.input_devices()) == 2 and fake_sd.restarts == 1
    rec.start()
    fake_sd.devices.append({"name": "USB Mic", "hostapi": 0, "max_input_channels": 1,
                            "default_samplerate": 44100.0})
    rec.input_devices()
    assert fake_sd.restarts == 1                   # во время записи не трогаем
    rec.stop()
    assert [m["name"] for m in rec.input_devices()][-1] == "USB Mic" and fake_sd.restarts == 2
