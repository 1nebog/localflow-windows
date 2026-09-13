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


def test_stereo_is_mixed_to_mono():
    rec = AudioRecorder(stream_factory=lambda **kw: FakeStream(**kw))
    rec.start()
    stereo = np.stack([np.full(800, 0.2, np.float32), np.zeros(800, np.float32)], axis=1)
    rec._callback(stereo, 800, None, None)
    assert np.allclose(rec.stop(), 0.1)
