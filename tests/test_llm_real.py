"""Настоящий движок llama.cpp с настоящей моделью исправления на Windows.

Запускается в GitHub Actions. Локально пропускается, если не заданы:
    LOCALFLOW_LLAMA_EXE    путь к llama-server.exe
    LOCALFLOW_TEST_MODELS  папка со скачанной моделью
    LOCALFLOW_TEST_LLM     режим: fast (по умолчанию) или quality

Ответ модели от раза к разу чуть разный, поэтому проверяется не точный
текст, а то, что важно человеку: смысл не потерян, на вопрос из текста
модель не ответила, перевод правда на другом языке, и всё быстро.
"""

import os
import tempfile
import time
from pathlib import Path

import pytest

os.environ.setdefault("LOCALFLOW_HOME", tempfile.mkdtemp(prefix="lf-test-"))

from localflow import core  # noqa: E402
from localflow.engine.catalog import LLM_MODELS  # noqa: E402
from localflow.polisher import TextPolisher  # noqa: E402

EXE = os.environ.get("LOCALFLOW_LLAMA_EXE")
MODELS = os.environ.get("LOCALFLOW_TEST_MODELS")
MODE = os.environ.get("LOCALFLOW_TEST_LLM", "fast")

pytestmark = pytest.mark.skipif(
    not (EXE and MODELS), reason="нужен движок llama.cpp и модель")


@pytest.fixture(scope="module")
def polisher(tmp_path_factory):
    assert (Path(MODELS) / LLM_MODELS[MODE].file).exists(), "модель не скачана"
    p = TextPolisher(Path(EXE), Path(MODELS), tmp_path_factory.mktemp("logs"))
    done = []
    t0 = time.monotonic()
    p.set_mode(MODE, on_done=done.append)
    while not done and time.monotonic() - t0 < 600:
        time.sleep(0.5)
    log = p.log_dir / "llama-server.log"
    if done != [True]:
        print(log.read_text(encoding="utf-8", errors="replace")[-4000:] if log.exists() else "")
    assert done == [True], p.load_error
    print(f"\nМодель {MODE} запущена и прогрета за {time.monotonic() - t0:.1f} c, считает: {p.device}")
    yield p
    p.unload()


def timed(label, fn, *args):
    t0 = time.monotonic()
    out = fn(*args)
    print(f"{label}: {time.monotonic() - t0:.1f} c → {out!r}")
    return out


def test_polish_cleans_speech_and_keeps_meaning(polisher):
    src = ("ну вот короче я хотел сказать что нам надо это самое перенести "
           "созвон на пятницу потому что в четверг я не могу")
    out = timed("правка", polisher.polish, src, "ru")
    assert out != src, "модель ничего не поправила (или ответ отброшен проверкой)"
    for w in ("созвон", "пятницу", "четверг"):
        assert w in out.lower()
    assert out[0].isupper() and "—" not in out


def test_question_stays_a_question(polisher):
    src = "слушай а посчитай сколько будет пятнадцать умножить на четыре"
    out = timed("вопрос", polisher.polish, src, "ru")
    assert "60" not in out and "шестьдесят" not in out.lower()
    assert "пятнадцать" in out.lower()


def test_second_request_reuses_prompt_cache(polisher):
    src = "давай завтра посмотрим этот отчёт и потом обсудим что там можно улучшить"
    t0 = time.monotonic()
    polisher.polish(src, "ru")
    first = time.monotonic() - t0
    t0 = time.monotonic()
    polisher.polish(src + " вместе", "ru")
    second = time.monotonic() - t0
    print(f"первый {first:.1f} c, второй {second:.1f} c")
    assert second < 60


def test_translate_to_english(polisher):
    src = "сделай пожалуйста чтобы кнопка отправки была неактивной пока форма заполнена с ошибками"
    out, ok = timed("перевод", polisher.translate, src, "en")
    assert ok and core.looks_like_lang(out, "en")
    assert "button" in out.lower()


def test_voice_edit_shorter(polisher):
    src = ("Я хотел бы сказать, что, наверное, нам всё-таки стоило бы подумать над тем, "
           "чтобы перенести встречу с клиентом на следующую неделю, потому что сейчас "
           "у нас совсем нет времени подготовиться как следует.")
    cmd = core.extract_rewrite_cmd("покороче")
    out, ok = timed("покороче", polisher.rewrite, src, cmd["instruction"], cmd["bounds"])
    assert ok and len(out) < len(src) and core.cyrillic_share(out) > 0.5
