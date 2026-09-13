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
    from localflow import app, menu

    a = app.App()
    assert a.keys.hotkey == (VK_RCONTROL,)
    assert a.dictation.paste_method == "clipboard"
    assert menu.tray_state(a) == "loading"
    assert [i.label for i in menu.build(a) if i][-1]


# --- Трей и таблетка ----------------------------------------------------------

def test_ui_loop_runs_posts_and_timers():
    import threading

    from localflow.win import ui

    loop = ui.UiLoop()
    hits = []
    th = threading.Thread(target=lambda: loop.post(lambda: hits.append("post")))
    th.start()
    th.join()
    tick = loop.every(0.02, lambda: hits.append("tick"))
    loop.after(0.01, lambda: hits.append("once"))
    loop.pump(0.4)
    loop.cancel(tick)
    assert "post" in hits and hits.count("once") == 1 and hits.count("tick") >= 3
    loop.destroy()


def test_gdi_text_mask():
    from localflow.win import overlay

    m = overlay.GdiText().mask("Распознаю…", 14)
    assert m.shape[0] >= 14 and m.shape[1] > 40 and m.max() > 0.9


def test_pill_shows_on_top_without_stealing_focus(window):
    from ctypes import wintypes

    from localflow.win import overlay, ui, w32

    root, _, _ = window
    loop = ui.UiLoop()
    p = overlay.PillWindow(loop)
    before = user32.GetForegroundWindow()
    p.show_text("Говорите…")
    for i in range(10):
        p.show_wave([i / 10] * 20, locked=True, glow="red")
    loop.pump(0.6)
    pump(root, 0.05)
    assert user32.IsWindowVisible(wintypes.HWND(p.hwnd))
    assert user32.GetForegroundWindow() == before          # фокус не отнят
    user32.GetWindowLongW.restype = ctypes.c_long
    ex = user32.GetWindowLongW(wintypes.HWND(p.hwnd), -20)  # GWL_EXSTYLE
    for flag in (w32.WS_EX_TOPMOST, w32.WS_EX_NOACTIVATE, w32.WS_EX_TRANSPARENT,
                 w32.WS_EX_TOOLWINDOW, w32.WS_EX_LAYERED):
        assert ex & flag
    assert p.frames >= 5                                     # анимация идёт
    rect = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(p.hwnd), ctypes.byref(rect))
    work, _ = w32.monitor_at_cursor()
    assert work.left <= rect.left and rect.right <= work.right and rect.bottom <= work.bottom

    p.hide()
    loop.pump(0.2)
    assert not user32.IsWindowVisible(wintypes.HWND(p.hwnd))
    frames = p.frames
    loop.pump(0.3)
    assert p.frames == frames                                # спрятана — не рисуется
    p.destroy()
    loop.destroy()


def test_tray_icon_menu_and_notification():
    from localflow.menu import Item
    from localflow.win import tray, ui, w32

    loop = ui.UiLoop()
    t = tray.Tray(loop, lambda: [])
    hicon = t._icon("recording")
    assert hicon
    actions = {}
    items = [Item("статус", enabled=False), None, Item("a & b", lambda: None, checked=True),
             Item("подменю", children=[Item("один", lambda: None), Item("два", lambda: None)])]
    hmenu = tray.Tray._make_menu(items, actions)
    assert hmenu and sorted(k for k, v in actions.items() if v) == [2, 3, 4]
    w32.user32.DestroyMenu(hmenu)
    if not t.add("loading", "LocalFlow — проверка"):
        t.remove()
        loop.destroy()
        pytest.skip("на этой машине нет панели задач")
    t.update("idle", "LocalFlow — готово")
    t.notify("LocalFlow", "проверка уведомления")
    loop.pump(0.2)
    t.remove()
    loop.destroy()


def test_app_runs_with_tray_and_quits():
    """Весь запуск: трей, таблетка, перехват клавиш — и чистый выход из меню."""
    from localflow import app, menu
    from localflow.win import ui

    a = app.App()

    def no_model(progress=None):
        raise OSError("нет сети в проверке")

    a.autotune.ensure_start_model = no_model
    original = ui.UiLoop.__init__
    states = []

    def patched(self):
        original(self)
        self.after(1.5, lambda: states.append(menu.tray_state(a)))
        self.after(1.6, lambda: a.pill.show_text("Проверка"))
        self.after(2.2, a.quit)

    ui.UiLoop.__init__ = patched
    try:
        a.run()
    finally:
        ui.UiLoop.__init__ = original
    assert states == ["error"]                 # модель не скачалась — значок с ошибкой
    assert a.pill.frames > 0
    assert a._shut
    for _ in range(40):                        # перехват снимается в своём потоке
        if not a.hook._hook:
            break
        time.sleep(0.05)
    assert not a.hook._hook
