"""Умное исправление без настоящей модели: режимы, скачивание, отказ
видеокарты, проверки ответа модели."""

import dataclasses
import threading
import time
from pathlib import Path

import pytest

from localflow import core
from localflow.engine.catalog import LLM_MODELS
from localflow.engine.download import DownloadError
from localflow.engine.llama_server import EngineError, _THINK_RE
from localflow.polisher import TextPolisher


class FakeServer:
    instances: list = []
    fail_gpu = False           # движок не стартует на видеокарте
    crash_next = False         # следующий запрос роняет движок
    answer = None              # что ответит модель: fn(текст) -> ответ

    def __init__(self, exe, model, log_path, use_gpu=True):
        self.model = Path(model)
        self.use_gpu = use_gpu
        self.device = "Test GPU" if use_gpu else "CPU"
        self.alive = False
        self.requests = []
        FakeServer.instances.append(self)

    @property
    def running(self):
        return self.alive

    def start(self):
        if self.use_gpu and FakeServer.fail_gpu:
            raise EngineError("vulkan упал")
        self.alive = True

    def stop(self):
        self.alive = False

    def chat(self, messages, max_tokens, timeout=None):
        self.requests.append((messages, max_tokens))
        if FakeServer.crash_next:
            FakeServer.crash_next = False
            self.alive = False
            raise EngineError("упал")
        user = messages[-1]["content"]
        if FakeServer.answer is not None:
            return FakeServer.answer(user)
        return user


def wait(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture(autouse=True)
def tiny_models(monkeypatch):
    """Модели по килобайту: на Windows файл в 5 ГБ правда занимает 5 ГБ."""
    for mode, m in list(LLM_MODELS.items()):
        monkeypatch.setitem(LLM_MODELS, mode, dataclasses.replace(m, size=1000 + len(mode)))


@pytest.fixture
def models(tmp_path):
    return tmp_path / "models"


def put_model(models, mode):
    m = LLM_MODELS[mode]
    models.mkdir(exist_ok=True)
    (models / m.file).write_bytes(b"\0" * m.size)


@pytest.fixture
def make(models, tmp_path):
    FakeServer.instances = []
    FakeServer.fail_gpu = False
    FakeServer.crash_next = False
    FakeServer.answer = None
    downloads = []

    def downloader(model, folder, progress=None, cancelled=None):
        downloads.append(model.file)
        for i in range(5):
            if cancelled and cancelled():
                raise DownloadError("Скачивание отменено")
            if progress:
                progress(i * model.size // 4, model.size)
            time.sleep(0.02)
        put_model(folder, next(k for k, v in LLM_MODELS.items() if v is model))
        return folder / model.file

    def _make():
        p = TextPolisher(tmp_path / "llama-server.exe", models, tmp_path / "logs",
                         server_factory=FakeServer, downloader=downloader)
        p.downloads = downloads
        return p
    return _make


def switch(p, mode):
    done = []
    p.set_mode(mode, on_done=done.append)
    assert wait(lambda: done), "режим не переключился"
    return done[0]


def test_off_by_default_nothing_loaded(make):
    p = make()
    assert p.mode == "off" and not p.enabled and not p.ready
    assert p.polish("ну вот смотри тут текст", "ru") == "ну вот смотри тут текст"
    assert FakeServer.instances == []


def test_downloads_then_starts_and_warms_prompt(make, models):
    p = make()
    p.set_mode("fast")
    assert wait(lambda: p.progress is not None)
    assert wait(lambda: p.ready)
    assert p.downloads == [LLM_MODELS["fast"].file] and p.progress is None
    srv = FakeServer.instances[-1]
    assert srv.model.name == LLM_MODELS["fast"].file
    assert wait(lambda: srv.requests)       # прогрев: инструкция + примеры
    msgs, max_tokens = srv.requests[0]
    assert msgs[0]["role"] == "system" and msgs[0]["content"] == core._LLM_SYSTEM
    assert max_tokens == 1
    assert wait(lambda: not p.is_loading)


def test_already_downloaded_model_is_not_downloaded_again(make, models):
    put_model(models, "fast")
    p = make()
    assert switch(p, "fast") is True
    assert p.downloads == [] and p.downloaded("fast") and not p.downloaded("quality")


def test_switch_while_downloading_cancels_and_loads_last_choice(make, models):
    put_model(models, "fast")
    p = make()
    done = []
    p.set_mode("quality", on_done=done.append)
    assert wait(lambda: p.progress is not None)
    p.set_mode("fast", on_done=done.append)
    assert wait(lambda: done)
    assert done == [True] and p.ready
    assert FakeServer.instances[-1].model.name == LLM_MODELS["fast"].file
    assert not p.downloaded("quality")          # недокачанное не выдаётся за готовое


def test_off_unloads_and_frees_memory(make, models):
    put_model(models, "fast")
    p = make()
    switch(p, "fast")
    srv = FakeServer.instances[-1]
    assert switch(p, "off") is True
    assert not p.ready and not srv.alive


def test_gpu_failure_falls_back_to_cpu_and_is_remembered(make, models):
    put_model(models, "fast")
    FakeServer.fail_gpu = True
    p = make()
    saved = []
    p.on_gpu_disabled = lambda: saved.append(True)
    assert switch(p, "fast") is True
    assert p.device == "CPU" and not p.use_gpu and saved == [True]


def test_crash_during_polish_returns_original_and_restarts_on_cpu(make, models):
    put_model(models, "fast")
    p = make()
    switch(p, "fast")
    FakeServer.crash_next = True
    text = "ну короче я думаю что это надо переделать"
    assert p.polish(text, "ru") == text
    assert not p.ready and not p.use_gpu
    assert switch(p, "fast") is True and p.device == "CPU"


def test_polish_keeps_original_when_model_answers_the_question(make, models):
    put_model(models, "fast")
    p = make()
    switch(p, "fast")
    src = "слушай а посчитай сколько будет два плюс два умножить на десять"
    FakeServer.answer = lambda user: "Два плюс два умножить на десять равно 22."
    assert p.polish(src, "ru") == src
    FakeServer.answer = lambda user: "Слушай, а посчитай, сколько будет два плюс два умножить на десять?"
    assert p.polish(src, "ru") == "Слушай, а посчитай, сколько будет два плюс два умножить на десять?"


def test_polish_splits_lines_and_passes_context(make, models):
    put_model(models, "fast")
    p = make()
    switch(p, "fast")
    srv = FakeServer.instances[-1]
    srv.requests.clear()
    FakeServer.answer = lambda user: user.split("\n")[-1].capitalize() + "."
    out = p.polish("первая строка текста\n\nвторая строка текста", "ru",
                   {"kind": "code", "title": "app.py", "app": "Code"})
    assert out == "Первая строка текста.\n\nВторая строка текста."
    assert len(srv.requests) == 2
    assert srv.requests[0][0][-1]["content"].startswith("[куда: редактор кода — «app.py»")


def test_translate_checks_language(make, models):
    put_model(models, "fast")
    p = make()
    switch(p, "fast")
    src = "сделай чтобы кнопка была неактивной пока форма невалидная"
    FakeServer.answer = lambda user: "Disable the button while the form is invalid."
    assert p.translate(src, "en") == ("Disable the button while the form is invalid.", True)
    FakeServer.answer = lambda user: "Сделай кнопку неактивной, пока форма невалидная."
    assert p.translate(src, "en") == (src, False)      # «перевёл» русский в русский
    srv = FakeServer.instances[-1]
    assert "английский" in srv.requests[-1][0][0]["content"]


def test_rewrite_bounds(make, models):
    put_model(models, "fast")
    p = make()
    switch(p, "fast")
    src = "Я хотел бы сказать, что, наверное, нам стоило бы подумать над цветом кнопки."
    cmd = core.extract_rewrite_cmd("покороче")
    FakeServer.answer = lambda user: "Стоит поменять цвет кнопки."
    assert p.rewrite(src, cmd["instruction"], cmd["bounds"]) == ("Стоит поменять цвет кнопки.", True)
    FakeServer.answer = lambda user: src + " " + src
    assert p.rewrite(src, cmd["instruction"], cmd["bounds"]) == (src, False)


def test_think_block_is_stripped():
    assert _THINK_RE.sub("", "<think>\nхм\n</think>\n\nПривет.") == "Привет."
    assert _THINK_RE.sub("", "Привет.") == "Привет."


def test_set_mode_is_thread_safe(make, models):
    put_model(models, "fast")
    put_model(models, "quality")
    p = make()
    threads = [threading.Thread(target=p.set_mode, args=(m,))
               for m in ("fast", "quality", "off", "fast", "quality")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert wait(lambda: not p.is_loading)
    if p.mode == "off":
        assert not p.ready
    else:
        assert p.ready and FakeServer.instances[-1].model.name == LLM_MODELS[p.mode].file
    assert sum(s.alive for s in FakeServer.instances) == 1
