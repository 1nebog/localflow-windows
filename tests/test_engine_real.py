"""Настоящий движок whisper.cpp на настоящем Windows.

Запускается в GitHub Actions после сборки движка. Локально пропускается,
если не заданы переменные:
    LOCALFLOW_ENGINE_EXE   путь к whisper-server.exe
    LOCALFLOW_TEST_MODELS  папка со скачанными моделями
    LOCALFLOW_TEST_MODEL   ключ модели (по умолчанию base)
"""

import os
import tempfile
import time
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("LOCALFLOW_HOME", tempfile.mkdtemp(prefix="lf-test-"))

from localflow.audio_util import read_wav  # noqa: E402
from localflow.core import SAMPLE_RATE  # noqa: E402
from localflow.engine.whisper_server import WhisperServer  # noqa: E402
from localflow.transcriber import Transcriber  # noqa: E402

EXE = os.environ.get("LOCALFLOW_ENGINE_EXE")
MODELS = os.environ.get("LOCALFLOW_TEST_MODELS")
MODEL = os.environ.get("LOCALFLOW_TEST_MODEL", "base")
FIX = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.skipif(
    not (EXE and MODELS), reason="нужен собранный движок и модели")


def silence(sec):
    return np.zeros(int(sec * SAMPLE_RATE), dtype=np.float32)


def found(text, stems):
    low = text.lower()
    return [s for s in stems if s in low]


class CountingServer(WhisperServer):
    requests = 0

    def inference(self, *a, **kw):
        CountingServer.requests += 1
        return super().inference(*a, **kw)


@pytest.fixture(scope="module", params=["gpu-allowed", "cpu-only"])
def tr(request, tmp_path_factory):
    logs = tmp_path_factory.mktemp("logs")
    t = Transcriber(Path(EXE), Path(MODELS), logs, model_name=MODEL,
                    server_factory=CountingServer)
    t.use_gpu = request.param == "gpu-allowed"
    t0 = time.monotonic()
    assert t.load(), (logs / "whisper-server.log").read_text(errors="replace")
    print(f"\n[{request.param}] модель {MODEL} загружена за "
          f"{time.monotonic() - t0:.1f} c, считает: {t.device}")
    print("--- журнал движка (начало) ---")
    print("\n".join((logs / "whisper-server.log").read_text(
        errors="replace").splitlines()[:60]))
    yield t
    t.unload()


def timed(label, fn, audio):
    t0 = time.monotonic()
    out = fn(audio)
    dt = time.monotonic() - t0
    sec = len(audio) / SAMPLE_RATE
    print(f"\n{label}: {sec:.1f} c звука за {dt:.1f} c "
          f"(в {sec / dt:.1f}× быстрее реального времени): {out!r}")
    return out


def test_russian(tr):
    tr.language = "ru"
    text = timed("ru", tr.transcribe, read_wav(FIX / "ru.wav"))
    assert len(found(text, ["привет", "провер", "распозна", "движ", "диктов"])) >= 3
    assert tr.last_language == "ru"


def test_english_auto_language(tr):
    tr.language = "auto"
    text = timed("en/auto", tr.transcribe, read_wav(FIX / "en.wav"))
    assert len(found(text, ["speech", "recognition", "test", "engine", "computer"])) >= 3
    assert tr.last_language == "en"


def test_silence_at_start_no_prompt_echo(tr):
    tr.language = "ru"
    audio = np.concatenate([silence(2.0), read_wav(FIX / "ru.wav")])
    text = timed("тишина в начале", tr.transcribe, audio)
    assert not found(text, ["аккуратная диктовка", "as is", "английские слова", "термины"])
    assert len(found(text, ["провер", "распозна", "движ", "диктов"])) >= 2


# Чистую тишину здесь не проверяем: на ней Whisper выдумывает фразы
# («АПЛОДИСМЕНТЫ»), и программа до распознавания такую запись не доводит —
# её отсекает проверка громкости сразу после записи, как на Mac.


def test_long_dictation_windows(tr):
    """~70 секунд: три окна, ни одна часть не потерялась."""
    tr.language = "ru"
    ru, ru2 = read_wav(FIX / "ru.wav"), read_wav(FIX / "ru2.wav")
    parts = []
    while sum(len(p) for p in parts) < 70 * SAMPLE_RATE:
        parts += [ru, silence(1.0), ru2, silence(1.0)]
    audio = np.concatenate(parts)
    before = CountingServer.requests
    text = timed("длинная диктовка", tr.transcribe, audio)
    reps = sum(1 for p in parts if p is ru2)
    assert CountingServer.requests - before >= 3
    assert text.lower().count("син") >= reps - 1, text
    assert text.lower().count("провер") >= reps - 1, text
