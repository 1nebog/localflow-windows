"""Меню трея и состояние значка без Windows."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from localflow import core, menu, strings
from localflow.engine.catalog import WHISPER_MODELS
from localflow.keys import VK_RCONTROL

strings.install()


class Calls(list):
    def __getattr__(self, name):
        return lambda *a: self.append((name, *a))


def make_app(tmp_path, **kw):
    calls = Calls()
    tr = SimpleNamespace(is_ready=True, is_loading=False, load_error=None,
                         model_name="small", language="auto", models_dir=Path(tmp_path))
    app = SimpleNamespace(
        transcriber=tr,
        recorder=SimpleNamespace(is_recording=False),
        dictation=SimpleNamespace(_busy=False, style="auto", translate_to="off",
                                  paste_method="clipboard", history=[]),
        autotune=SimpleNamespace(auto=True, status="", progress=None),
        keys=SimpleNamespace(hotkey=(VK_RCONTROL,)),
        paused=False, started=True, download=None, hook_error=None,
        choose_model=calls.choose_model, set_language=calls.set_language,
        set_style=calls.set_style, set_translate=calls.set_translate,
        set_paste=calls.set_paste, set_ui_lang=calls.set_ui_lang,
        paste_from_history=calls.paste, toggle_pause=calls.pause, quit=calls.quit,
    )
    for k, v in kw.items():
        setattr(app, k, v)
    return app, calls


@pytest.fixture(autouse=True)
def russian():
    old = core._UI_LANG
    core._UI_LANG = "ru"
    yield
    core._UI_LANG = old


def find(items, label_start):
    for it in items:
        if it is not None and it.label.startswith(label_start):
            return it
    raise AssertionError(f"нет пункта {label_start!r}: {[i and i.label for i in items]}")


def test_ready_status_names_the_key(tmp_path):
    app, _ = make_app(tmp_path)
    assert menu.tray_state(app) == "idle"
    assert menu.status_text(app) == "Готово — зажми правый Ctrl и говори"
    assert menu.tooltip(app).startswith("LocalFlow — ")
    core._UI_LANG = "de"
    assert "Ctrl rechts" in menu.status_text(app)


@pytest.mark.parametrize("change, state, text", [
    ({"paused": True}, "paused", "На паузе"),
    ({"recorder": SimpleNamespace(is_recording=True)}, "recording", "Слушаю…"),
    ({"hook_error": "SetWindowsHookEx: 5"}, "error", "Клавиша диктовки не работает"),
])
def test_states(tmp_path, change, state, text):
    app, _ = make_app(tmp_path, **change)
    assert menu.tray_state(app) == state
    assert menu.status_text(app) == text


def test_first_download_shows_percent(tmp_path):
    app, _ = make_app(tmp_path, started=False, download=("base", 30, 60))
    app.transcriber.is_ready = False
    assert menu.tray_state(app) == "loading"
    assert menu.status_text(app) == "Скачиваю модель base… 50%"


def test_model_error_and_sleeping_model(tmp_path):
    app, _ = make_app(tmp_path, started=False)
    app.transcriber.is_ready = False
    app.transcriber.load_error = "движок не запустился"
    assert menu.tray_state(app) == "error"
    assert menu.status_text(app) == "Модель не запустилась"
    # модель выгрузилась от простоя — это не ошибка и не загрузка
    app.started, app.transcriber.load_error = True, None
    assert menu.tray_state(app) == "idle"


def test_background_switch_keeps_working_icon(tmp_path):
    app, _ = make_app(tmp_path)
    app.autotune.status = "switch:large-v3-turbo"
    assert menu.tray_state(app) == "idle"
    assert menu.status_text(app) == "Перехожу на модель large turbo…"


def test_menu_layout_and_actions(tmp_path):
    app, calls = make_app(tmp_path)
    items = menu.build(app)
    assert not items[0].enabled and items[1] is None
    labels = [i and i.label for i in items]
    assert labels[-2:] == ["Пауза", "Выход"]

    models = find(items, "Модель").children
    assert models[0].label == "Авто · small" and models[0].checked
    size = WHISPER_MODELS["medium"].size_mb
    assert find(models, "medium").label == f"medium · {size} МБ"   # не скачана
    find(models, "tiny").action()
    models[0].action()

    langs = find(items, "Язык").children
    assert langs[0].label == "Определять сам" and langs[0].checked
    find(langs, "Английский").action()
    find(find(items, "Стиль текста").children, "Чат").action()
    tr_items = find(items, "Диктовка с переводом").children
    assert tr_items[0].checked and tr_items[0].label == "Выключено"
    find(tr_items, "Немецкий").action()
    find(find(items, "Метод").children, "Печать").action()
    find(find(items, "Язык интерфейса").children, "English").action()
    find(items, "Пауза").action()
    find(items, "Выход").action()
    assert calls == [("choose_model", "tiny"), ("choose_model", None),
                     ("set_language", "en"), ("set_style", "chat"),
                     ("set_translate", "de"), ("set_paste", "type"),
                     ("set_ui_lang", "en"), ("pause",), ("quit",)]


def test_manual_model_is_checked(tmp_path):
    app, _ = make_app(tmp_path)
    app.autotune.auto = False
    models = find(menu.build(app), "Модель").children
    assert models[0].label == "Авто" and not models[0].checked
    assert find(models, "small").checked


def test_history_items(tmp_path):
    long = "очень " * 30
    app, calls = make_app(tmp_path)
    assert find(menu.build(app), "История").children[0].enabled is False
    app.dictation.history = [{"text": "Первая\nстрока"}, {"text": long}] + [{"text": str(i)} for i in range(20)]
    hist = find(menu.build(app), "История").children
    assert len(hist) == menu.HISTORY_ITEMS
    assert hist[0].label == "Первая строка"
    assert len(hist[1].label) == menu.HISTORY_CHARS and hist[1].label.endswith("…")
    hist[1].action()
    assert calls == [("paste", long)]


def test_menu_follows_interface_language(tmp_path):
    app, _ = make_app(tmp_path)
    core._UI_LANG = "en"
    labels = [i.label for i in menu.build(app) if i]
    assert "Quit" in labels and "Pause" in labels
    assert labels[0] == "Ready — hold Right Ctrl and speak"
