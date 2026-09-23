"""Логика распознавателя без настоящего движка: нарезка окон, подсказка,
спасение хвоста, переход на процессор, чистка текста."""

import os
import tempfile
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("LOCALFLOW_HOME", tempfile.mkdtemp(prefix="lf-test-"))

from localflow.core import SAMPLE_RATE  # noqa: E402
from localflow.engine.whisper_server import EngineError  # noqa: E402
from localflow.transcriber import Transcriber  # noqa: E402


def speech(seconds: float, level: float = 0.3) -> np.ndarray:
    """«Речь»: громкий шум нужной длины."""
    rng = np.random.default_rng(0)
    return (rng.uniform(-level, level, int(seconds * SAMPLE_RATE))).astype(np.float32)


class FakeServer:
    """Движок-заглушка. script(seconds, fields, call_no) -> ответ движка."""

    instances: list["FakeServer"] = []
    fail_gpu_start = False

    def __init__(self, exe, model, log_path, use_gpu=True, no_speech_thold=0.85):
        self.use_gpu = use_gpu
        self.calls = []
        self.device = "Fake GPU" if use_gpu else "CPU"
        self._running = False
        FakeServer.instances.append(self)

    @property
    def running(self):
        return self._running

    def start(self):
        if self.use_gpu and FakeServer.fail_gpu_start:
            raise EngineError("vulkan driver crash")
        self._running = True

    def stop(self):
        self._running = False

    def inference(self, wav, fields, timeout=0):
        seconds = (len(wav) - 44) / 2 / SAMPLE_RATE
        self.calls.append({"sec": round(seconds, 2), **fields})
        return FakeServer.script(seconds, fields, len(self.calls))

    @staticmethod
    def script(seconds, fields, n):
        return {"language": "russian", "segments": []}


@pytest.fixture
def make(tmp_path, monkeypatch):
    FakeServer.instances.clear()
    FakeServer.fail_gpu_start = False

    def _make(script):
        monkeypatch.setattr(FakeServer, "script", staticmethod(script))
        t = Transcriber(tmp_path / "whisper-server.exe", tmp_path, tmp_path,
                        model_name="base", server_factory=FakeServer)
        t.language = "ru"
        assert t.load()
        for server in FakeServer.instances:  # прогрев не считаем
            server.calls.clear()
        return t

    return _make


def real_calls(t):
    """Вызовы после прогрева."""
    return [c for s in FakeServer.instances for c in s.calls]


def test_short_audio_one_request_with_prompt(make):
    def script(sec, fields, n):
        return {"language": "russian",
                "segments": [{"start": 0.0, "end": sec, "text": " привет мир"}]}

    t = make(script)
    out = t._decode(speech(10), "ru", "ПОДСКАЗКА")
    calls = real_calls(t)
    assert len(calls) == 1
    assert calls[0]["prompt"] == "ПОДСКАЗКА"
    assert calls[0]["language"] == "ru"
    assert out["language"] == "ru"
    assert out["text"].strip() == "привет мир"


def test_long_audio_windows_prompt_only_first(make):
    def script(sec, fields, n):
        return {"language": "russian",
                "segments": [{"start": 0.0, "end": sec - 0.8, "text": f" кусок{n}"}]}

    t = make(script)
    out = t._decode(speech(75), None, "ПОДСКАЗКА")
    calls = real_calls(t)
    assert [c["sec"] for c in calls] == [30.0, 30.0, 15.0]
    assert "prompt" in calls[0] and all("prompt" not in c for c in calls[1:])
    # язык определяется один раз, дальше окна идут с уже известным языком
    assert [c["language"] for c in calls] == ["auto", "ru", "ru"]
    # время сегментов — от начала всей записи, а не окна
    assert [round(s["start"]) for s in out["segments"]] == [0, 30, 60]


def test_phrase_cut_by_window_is_redecoded(make):
    def script(sec, fields, n):
        if n == 1:  # первое окно: фраза упёрлась в границу
            return {"language": "russian", "segments": [
                {"start": 0.0, "end": 10.0, "text": " первая"},
                {"start": 10.0, "end": 29.9, "text": " разрез"}]}
        return {"language": "russian",
                "segments": [{"start": 0.0, "end": sec - 0.8, "text": " целиком"}]}

    t = make(script)
    out = t._decode(speech(45), "ru", None)
    # второе окно начато с 10 c (а не с 30), третье добирает остаток
    assert [c["sec"] for c in real_calls(t)] == [30.0, 30.0, 5.0]
    assert [round(s["start"]) for s in out["segments"]] == [0, 10, 40]
    assert "разрез" not in out["text"]


def test_decoder_stopped_early_rest_goes_to_next_window(make):
    def script(sec, fields, n):
        if n == 1:
            return {"language": "russian",
                    "segments": [{"start": 0.0, "end": 12.0, "text": " начало"}]}
        return {"language": "russian",
                "segments": [{"start": 0.0, "end": sec - 0.8, "text": " продолжение"}]}

    t = make(script)
    t._decode(speech(40), "ru", None)
    assert real_calls(t)[1]["sec"] == 28.0    # 40 - 12


def test_tail_recovery(make):
    def script(sec, fields, n):
        if n == 1:  # основной прогон бросил хвост на 3 секунде из 10
            return {"language": "russian",
                    "segments": [{"start": 0.0, "end": 3.0, "text": " начало фразы"}]}
        return {"language": "russian",
                "segments": [{"start": 0.0, "end": sec, "text": " спасённый хвост"}]}

    t = make(script)
    text = t.transcribe(speech(10))
    assert "Спасённый хвост" in text or "спасённый хвост" in text
    assert "prompt" not in real_calls(t)[-1]  # хвосту подсказку не дают


def test_prompt_echo_and_cleanup(make):
    def script(sec, fields, n):
        return {"language": "russian", "segments": [
            {"start": 0.0, "end": 2.0,
             "text": " Английские слова пишем as is a result of the process."},
            {"start": 2.0, "end": sec, "text": " Кнопку красной, вернее, синей сделай."}]}

    t = make(script)
    assert t.transcribe(speech(6)) == "Кнопку синей сделай."
    assert t.last_language == "ru"


def test_editor_command_is_verbatim(make):
    def script(sec, fields, n):
        return {"language": "russian",
                "segments": [{"start": 0.0, "end": sec, "text": " Запятая."}]}

    t = make(script)
    assert t.transcribe(speech(2)) == ","
    assert t.last_verbatim


def test_gpu_failure_falls_back_to_cpu(make):
    FakeServer.fail_gpu_start = True
    saved = []

    def script(sec, fields, n):
        return {"language": "english",
                "segments": [{"start": 0.0, "end": sec, "text": " hello"}]}

    FakeServer.instances.clear()
    t = Transcriber(Path("x.exe"), Path("."), Path("."), model_name="base",
                    server_factory=FakeServer)
    t.on_gpu_disabled = lambda: saved.append(True)
    FakeServer.script = staticmethod(script)
    assert t.load()
    assert t.use_gpu is False and saved == [True]
    assert t.device == "CPU"


def test_engine_crash_mid_work_restarts_on_cpu(make):
    def script(sec, fields, n):
        return {"language": "russian",
                "segments": [{"start": 0.0, "end": sec, "text": " работает"}]}

    t = make(script)
    first = t._server

    def crash(*a, **kw):
        first._running = False
        raise EngineError("упал")

    first.inference = crash
    assert t._decode(speech(3), "ru", None)["text"].strip() == "работает"
    assert t.use_gpu is False and t._server is not first


def test_unload_stops_engine(make):
    t = make(lambda sec, fields, n: {"language": "russian", "segments": []})
    server = t._server
    t.unload()
    assert not server.running and not t.is_ready


def test_language_is_chosen_once_for_the_whole_dictation(make):
    """Длинная диктовка разбирается кусками. Если язык определять на каждом
    куске, середина русской речи может уехать на английский — человек
    получает перевод. Определяем один раз и держим до конца записи."""
    def script(sec, fields, n):
        # движок «услышал» английский на втором куске
        lang = "russian" if n == 1 else "english"
        return {"language": lang, "segments": [
            {"start": 0.0, "end": sec, "text": " Кусок."}]}

    t = make(script)
    t.language = "auto"
    t.start_session()
    t.raw_text(speech(5))
    t.raw_text(speech(5))
    t.raw_text(speech(5))
    langs = [c["language"] for c in real_calls(t)]
    assert langs == ["auto", "ru", "ru"]     # спросили один раз, дальше — держим
    assert t.last_language == "ru"


def test_new_dictation_detects_language_again(make):
    def script(sec, fields, n):
        return {"language": "english" if n > 1 else "russian",
                "segments": [{"start": 0.0, "end": sec, "text": " Кусок."}]}

    t = make(script)
    t.language = "auto"
    t.start_session()
    t.raw_text(speech(5))
    t.start_session()                        # новая диктовка
    t.raw_text(speech(5))
    assert [c["language"] for c in real_calls(t)] == ["auto", "auto"]
    assert t.last_language == "en"


def test_chosen_language_is_never_overridden(make):
    def script(sec, fields, n):
        return {"language": "english",
                "segments": [{"start": 0.0, "end": sec, "text": " Кусок."}]}

    t = make(script)                         # в make язык выставлен «ru» руками
    t.start_session()
    t.raw_text(speech(5))
    t.raw_text(speech(5))
    assert [c["language"] for c in real_calls(t)] == ["ru", "ru"]
