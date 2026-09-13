"""Вся цепочка на настоящей Windows: правый Ctrl → запись → движок → текст в окне.

Микрофона на сервере GitHub нет, поэтому «запись» — готовый файл с речью.
Всё остальное настоящее: перехват клавиатуры, движок whisper.cpp, чистка
текста, вставка через буфер обмена в окно другой программы.
"""

import os
import sys
import time
from pathlib import Path

import pytest

from conftest import pump

EXE = os.environ.get("LOCALFLOW_ENGINE_EXE")
MODELS = os.environ.get("LOCALFLOW_TEST_MODELS")
MODEL = os.environ.get("LOCALFLOW_TEST_MODEL", "base")

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or not (EXE and MODELS),
    reason="нужна Windows, собранный движок и модели")


class FileRecorder:
    """Микрофон, который «слышит» готовую запись."""

    def __init__(self, audio):
        self.audio = audio
        self.level = 0.0
        self._rec = False

    @property
    def is_recording(self):
        return self._rec

    def start(self):
        self._rec = True

    def stop(self):
        self._rec = False
        return self.audio

    def tail(self, sec):
        return self.audio[-int(sec * 16000):]


def test_right_ctrl_dictation_lands_in_window(window, tmp_path):
    from localflow.audio_util import read_wav
    from localflow.dictation import Dictation
    from localflow.keys import VK_RCONTROL, KeyboardLogic
    from localflow.transcriber import Transcriber
    from localflow.win import keyhook, paste, system

    root, text, focused = window
    if not focused:
        pytest.skip("окно не получило фокус на этой машине")
    text.delete("1.0", "end")

    tr = Transcriber(Path(EXE), Path(MODELS), tmp_path, model_name=MODEL)
    tr.language = "ru"
    assert tr.load()
    keys = KeyboardLogic(is_down=keyhook.is_key_down)
    audio = read_wav(Path(__file__).parent / "fixtures" / "ru.wav")
    d = Dictation(tr, FileRecorder(audio), keys, paste=paste.Paster(),
                  sounds=system.Sounds(tmp_path / "sounds"),
                  frontmost_app=system.frontmost_app_name,
                  window_title=system.frontmost_window_title)
    d.style = "normal"
    hook = keyhook.KeyHook(keys, d.on_key_event, accept_injected=True)
    assert hook.start()
    try:
        t0 = time.monotonic()
        paste._send([paste._key(VK_RCONTROL)])
        pump(root, 0.6)                                   # «говорит», держа Ctrl
        paste._send([paste._key(VK_RCONTROL, flags=paste.KEYEVENTF_KEYUP)])
        while time.monotonic() - t0 < 120 and not text.get("1.0", "end-1c"):
            pump(root, 0.2)
        got = text.get("1.0", "end-1c")
        print(f"\nот нажатия до текста в окне: {time.monotonic() - t0:.1f} c: {got!r}")
        low = got.lower()
        assert sum(w in low for w in ("провер", "распозна", "движ", "диктов")) >= 3, got
        assert d.history and d.history[0]["text"] == got
    finally:
        hook.stop()
        tr.unload()
