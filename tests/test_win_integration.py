"""Настоящая Windows: перехват клавиш, вставка, буфер обмена, активное окно.

Нажатия имитируются программно (SendInput), текст вставляется в настоящее
окно с полем ввода. На Mac и Linux пропускается.
"""

import ctypes
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="только Windows")

from conftest import pump  # noqa: E402

if sys.platform == "win32":
    from localflow.core import CLIPBOARD_RESTORE_DELAY
    from localflow.keys import (
        ENTER, HOTKEY_DOWN, HOTKEY_UP, VK_RCONTROL, VK_RETURN, KeyboardLogic,
    )
    from localflow.win import keyhook, paste, system

    user32 = ctypes.WinDLL("user32", use_last_error=True)


def content(text):
    return text.get("1.0", "end-1c")


@pytest.fixture
def hook():
    events = []
    logic = KeyboardLogic(is_down=keyhook.is_key_down)

    def on_event(ev):
        events.append(ev)

    h = keyhook.KeyHook(logic, on_event, accept_injected=True)
    assert h.start(), h.error
    yield logic, events
    h.stop()


def test_hotkey_press_and_release(window, hook):
    root, text, _ = window
    logic, events = hook
    paste._send([paste._key(VK_RCONTROL)])
    pump(root, 0.3)
    paste._send([paste._key(VK_RCONTROL, flags=paste.KEYEVENTF_KEYUP)])
    pump(root, 0.3)
    assert events[:2] == [HOTKEY_DOWN, HOTKEY_UP]


def test_enter_is_swallowed_only_while_recording(window, hook):
    root, text, focused = window
    if not focused:
        pytest.skip("окно не получило фокус на этой машине")
    logic, events = hook
    text.delete("1.0", "end")
    logic.enter_armed = True
    paste.press_combo(VK_RETURN)
    pump(root)
    assert ENTER in events
    assert content(text) == ""            # в окно Enter не попал
    logic.enter_armed = False
    paste.press_combo(VK_RETURN)
    pump(root)
    assert content(text) == "\n"


def test_paste_unicode_and_restore_clipboard(window):
    root, text, focused = window
    if not focused:
        pytest.skip("окно не получило фокус на этой машине")
    text.delete("1.0", "end")
    paste.set_text("старое содержимое", private=False)
    phrase = "Привет, ёжик! Grüße 👋"
    assert paste.paste_text(phrase, "clipboard")
    pump(root, 0.6)
    assert content(text) == phrase
    assert paste.get_text() == phrase
    time.sleep(CLIPBOARD_RESTORE_DELAY + 0.5)
    assert paste.get_text() == "старое содержимое"


def test_dictation_is_hidden_from_clipboard_history():
    paste.set_text("личное")
    fmt = user32.RegisterClipboardFormatW("CanIncludeInClipboardHistory")
    assert user32.IsClipboardFormatAvailable(fmt)


def test_type_text_ignores_keyboard_layout(window):
    root, text, focused = window
    if not focused:
        pytest.skip("окно не получило фокус на этой машине")
    text.delete("1.0", "end")
    assert paste.type_text("Съешь ещё\nбулок")
    pump(root, 0.6)
    assert content(text) == "Съешь ещё\nбулок"


def test_frontmost_window(window):
    root, _, focused = window
    if not focused:
        pytest.skip("окно не получило фокус на этой машине")
    assert system.frontmost_window_title() == "LocalFlow test window"
    assert system.frontmost_app_name().lower().startswith("python")


def test_sounds_and_language(tmp_path):
    s = system.Sounds(tmp_path)
    for name in ("start", "stop", "cancel"):
        assert (tmp_path / f"{name}.wav").stat().st_size > 1000
        s.play(name)                      # без звуковой карты — молча
    assert system.system_ui_lang() in ("ru", "uk", "de", "en")


def test_app_builds():
    from localflow import app

    a = app.App()
    assert a.keys.hotkey == (VK_RCONTROL,)
    assert a.dictation.paste_method == "clipboard"
