"""Панель без Windows: данные, настройки, назначение клавиши, защита сервера."""

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from localflow import core, icons, panel, strings
from localflow.keys import DEFAULT_HOTKEY, VK_ESCAPE, VK_LCONTROL, VK_RCONTROL, KeyboardLogic

strings.install()


class Calls(list):
    def __getattr__(self, name):
        return lambda *a: self.append((name, *a))


def make_app(tmp_path):
    calls = Calls()
    keys = KeyboardLogic()

    def set_hotkey(chord):
        calls.append(("set_hotkey", tuple(chord)))
        keys.set_hotkey(chord)

    history = [{"ts": 2.0, "text": "вторая", "app": "Telegram"}, {"ts": 1.0, "text": "первая"}]
    app = SimpleNamespace(
        transcriber=SimpleNamespace(is_ready=True, is_loading=False, load_error=None,
                                    model_name="base", language="auto", models_dir=Path(tmp_path)),
        recorder=SimpleNamespace(is_recording=False, device=None),
        dictation=SimpleNamespace(_busy=False, style="auto", translate_to="off",
                                  paste_method="clipboard", history=history,
                                  app_profiles={"Word": {"tone": "email"}}, idle_unload_min=10),
        autotune=SimpleNamespace(auto=True, status="", progress=None),
        keys=keys, sounds=SimpleNamespace(enabled=True), pill_animation="pulse",
        paused=False, started=True, download=None, hook_error=None,
        list_mics=lambda: [{"name": "USB Mic", "label": "USB Mic"}],
        autostart_enabled=lambda: False,
        set_hotkey=set_hotkey,
        choose_model=calls.choose_model, set_language=calls.set_language,
        set_style=calls.set_style, set_translate=calls.set_translate,
        set_paste=calls.set_paste, set_ui_lang=calls.set_ui_lang, set_mic=calls.set_mic,
        set_animation=calls.set_animation, set_idle_unload=calls.set_idle_unload,
        set_sounds=calls.set_sounds, set_autostart=calls.set_autostart,
        set_profiles=calls.set_profiles, delete_history=calls.delete_history,
        clear_history=calls.clear_history,
    )
    return app, calls


@pytest.fixture(autouse=True)
def russian():
    old = core._UI_LANG
    core._UI_LANG = "ru"
    yield
    core._UI_LANG = old


def test_panel_strings_have_same_keys():
    ru = set(panel.PANEL_STRINGS["ru"])
    for lang, items in panel.PANEL_STRINGS.items():
        assert set(items) == ru, lang
    for lang in ("ru", "en", "uk", "de"):
        for key in ("menu_settings", "mic_default", "idle_never"):
            assert core.UI_STRINGS[lang][key]


def test_snapshot(tmp_path):
    app, _ = make_app(tmp_path)
    data = json.loads(json.dumps(panel.snapshot(app), ensure_ascii=False))
    st, opt = data["settings"], data["options"]
    assert st["hotkey"] == "правый Ctrl" and st["hotkey_default"]
    assert st["model"] == "auto" and opt["model"][0] == ["auto", "Авто · base"]
    assert opt["mic"] == [["", "Как в системе"], ["USB Mic", "USB Mic"]]
    assert ["0", "Никогда"] in opt["idle_unload_min"] and st["idle_unload_min"] == "10"
    assert data["apps"] == ["Telegram", "Word"]
    assert data["history"][0] == {"ts": 2.0, "text": "вторая"}
    for key in ("language", "translate_to", "paste_method", "style", "pill_animation", "ui_lang"):
        assert st[key] in [v for v, _ in opt[key]], key


def test_apply_settings(tmp_path):
    app, calls = make_app(tmp_path)
    assert panel.apply(app, "model", "auto")
    assert panel.apply(app, "model", "small")
    assert panel.apply(app, "mic", "")
    assert panel.apply(app, "language", "en")
    assert panel.apply(app, "idle_unload_min", "30")
    assert panel.apply(app, "pill_animation", "orbit")
    assert panel.apply(app, "sounds", False)
    assert not panel.apply(app, "model", "gigantic")
    assert not panel.apply(app, "language", "xx")
    assert not panel.apply(app, "sounds", "yes")        # переключатель — только да/нет
    assert not panel.apply(app, "hack", "1")
    assert calls == [("choose_model", None), ("choose_model", "small"), ("set_mic", None),
                     ("set_language", "en"), ("set_idle_unload", 30),
                     ("set_animation", "orbit"), ("set_sounds", False)]


def test_clean_profiles_and_pairs():
    raw = {" Chrome ": {"paste_method": "type", "tone": "chat", "x": 1},
           "": {"tone": "chat"}, "Bad": {"paste_method": "rm -rf", "tone": "auto"}, "N": "?"}
    assert panel.clean_profiles(raw) == {"Chrome": {"paste_method": "type", "tone": "chat"}, "Bad": {}}
    assert panel.clean_pairs([["а", "б"], ["один"], "x", [1, 2]]) == [["а", "б"], ["1", "2"]]
    assert panel.clean_pairs("нет") == []


# --- Назначение клавиши -------------------------------------------------------

def press(keys, *vks):
    for vk in vks:
        assert keys.feed(vk, True)[0]            # во время назначения клавиши съедаются
    for vk in reversed(vks):
        keys.feed(vk, False)


def wait_state(cap, state="waiting"):
    for _ in range(100):
        s = cap.poll()
        if s["state"] != state:
            return s
        time.sleep(0.01)
    raise AssertionError("не дождались")


def test_capture_assigns_combination(tmp_path):
    app, calls = make_app(tmp_path)
    cap = panel.HotkeyCapture(app)
    assert cap.start()["state"] == "waiting" and app.keys.capturing
    press(app.keys, VK_LCONTROL, 0x20)            # Ctrl+Space
    s = wait_state(cap)
    assert s["state"] == "done" and s["label"] == "левый Ctrl+Space" and not s["default"]
    assert calls == [("set_hotkey", (VK_LCONTROL, 0x20))]


def test_capture_rejects_typing_key_and_warns_about_shift(tmp_path):
    app, calls = make_app(tmp_path)
    cap = panel.HotkeyCapture(app)
    cap.start()
    press(app.keys, 0x41)                         # просто «A»
    s = wait_state(cap)
    assert s["state"] == "error" and "набора текста" in s["msg"]
    assert s["label"] == "правый Ctrl" and calls == []
    cap.start()
    press(app.keys, 0xA1)                         # правый Shift — можно, но с оговоркой
    s = wait_state(cap)
    assert s["state"] == "done" and "Shift" in s["msg"]


def test_capture_escape_cancel_and_timeout(tmp_path):
    app, calls = make_app(tmp_path)
    now = [0.0]
    cap = panel.HotkeyCapture(app, clock=lambda: now[0])
    cap.start()
    press(app.keys, VK_ESCAPE)
    assert wait_state(cap)["state"] == "idle" and calls == []
    cap.start()
    cap.cancel()
    assert not app.keys.capturing and cap.poll()["state"] == "idle"
    cap.start()
    now[0] = panel.CAPTURE_TIMEOUT_SEC + 1
    assert cap.poll()["state"] == "idle" and not app.keys.capturing
    # после отмены клавиши снова обычные: правый Ctrl начинает диктовку
    assert app.keys.feed(VK_RCONTROL, True) == (False, ["hotkey_down"])
    assert app.keys.hotkey == DEFAULT_HOTKEY


def test_capture_needs_working_hook(tmp_path):
    app, _ = make_app(tmp_path)
    app.hook_error = "SetWindowsHookEx: 5"
    s = panel.HotkeyCapture(app).start()
    assert s["state"] == "error" and not app.keys.capturing


# --- Сервер --------------------------------------------------------------------

@pytest.fixture
def server(tmp_path):
    app, calls = make_app(tmp_path)
    srv = panel.PanelServer(app)
    assert srv.start()
    yield srv, app, calls
    srv.stop()


def request(srv, path, body=None, key=True, host=None, method=None):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-LocalFlow"] = srv.token
    if host:
        headers["Host"] = host
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{srv.port}{path}", data=data,
                                 headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_page_needs_secret_key(server):
    srv, _, _ = server
    assert request(srv, "/", key=False)[0] == 403
    assert request(srv, "/?k=wrong", key=False)[0] == 403
    code, html = request(srv, f"/?k={srv.token}", key=False)
    html = html.decode()
    assert code == 200 and srv.token in html and "__L__" not in html and "Настройки" in html
    code, png = request(srv, "/icon.png", key=False)
    assert code == 200 and png.startswith(b"\x89PNG")


def test_api_rejects_other_sites(server):
    srv, _, calls = server
    assert request(srv, "/api/all", key=False)[0] == 403
    assert request(srv, "/api/settings", {"key": "language", "value": "en"}, key=False)[0] == 403
    # чужое имя сайта, указывающее на 127.0.0.1 (DNS rebinding)
    assert request(srv, "/api/all", host=f"evil.example:{srv.port}")[0] == 403
    assert calls == []


def test_api_reads_and_changes(server):
    srv, app, calls = server
    code, body = request(srv, "/api/all")
    assert code == 200 and json.loads(body)["settings"]["language"] == "auto"
    code, body = request(srv, "/api/status")
    status = json.loads(body)
    assert status["state"] == "idle" and status["history_ts"] == 2.0
    assert json.loads(request(srv, "/api/settings", {"key": "language", "value": "de"})[1]) == \
        {"ok": True, "reload": False}
    assert json.loads(request(srv, "/api/settings", {"key": "ui_lang", "value": "en"})[1])["reload"]
    assert not json.loads(request(srv, "/api/settings", {"key": "model", "value": "x"})[1])["ok"]
    request(srv, "/api/profiles", {"profiles": {"Chrome": {"tone": "chat", "evil": 1}}})
    request(srv, "/api/history/delete", {"ts": 2.0})
    request(srv, "/api/history/clear", {})
    assert calls == [("set_language", "de"), ("set_ui_lang", "en"),
                     ("set_profiles", {"Chrome": {"tone": "chat"}}),
                     ("delete_history", 2.0), ("clear_history",)]
    assert request(srv, "/api/nope", {})[0] == 404
    assert request(srv, "/api/settings", method="POST")[0] in (200, 400)


def test_api_writes_dictionary(server):
    srv, _, _ = server
    request(srv, "/api/dict", {"pairs": [["локал флоу", "LocalFlow"], ["", "пусто"]]})
    assert core.read_pairs(core.DICTIONARY_PATH) == [["локал флоу", "LocalFlow"]]
    request(srv, "/api/snippets", {"pairs": [["моя подпись", "С уважением,\\nИмя"]]})
    assert core.read_pairs(core.SNIPPETS_PATH) == [["моя подпись", "С уважением,\\nИмя"]]
    assert str(core.DICTIONARY_PATH).startswith(__import__("os").environ["LOCALFLOW_HOME"])


def test_api_hotkey_flow(server):
    srv, app, calls = server
    assert json.loads(request(srv, "/api/hotkey/capture", {})[1])["state"] == "waiting"
    press(app.keys, VK_LCONTROL, 0x70)            # Ctrl+F1
    for _ in range(100):
        s = json.loads(request(srv, "/api/hotkey")[1])
        if s["state"] != "waiting":
            break
        time.sleep(0.01)
    assert s["state"] == "done" and s["label"] == "левый Ctrl+F1"
    s = json.loads(request(srv, "/api/hotkey/reset", {})[1])
    assert s["label"] == "правый Ctrl" and s["default"]
    assert calls[-1] == ("set_hotkey", DEFAULT_HOTKEY)


def test_app_icon_png():
    img = icons.app_icon(64)
    assert img.shape == (64, 64, 4) and img[0, 0, 3] == 0 and img[32, 5, 3] == 255
    assert (img[32, 32, :3] > 200).all() or img[32, 32, 2] > 200
    assert icons.png_bytes(img)[:8] == b"\x89PNG\r\n\x1a\n"


def test_auto_model_label_waits_for_first_start(tmp_path):
    app, _ = make_app(tmp_path)
    app.started, app.transcriber.is_ready = False, False
    assert panel.options(app)["model"][0] == ["auto", "Авто"]


def test_capture_ends_by_itself_when_panel_is_closed(tmp_path):
    app, _ = make_app(tmp_path)
    cap = panel.HotkeyCapture(app)
    cap.timeout = 0.05
    cap.start()
    for _ in range(100):
        if not app.keys.capturing:
            break
        time.sleep(0.01)
    assert not app.keys.capturing              # никто не спрашивал состояние — всё равно снято


def test_mic_list_endpoint_and_any_mic_name(server):
    srv, app, calls = server
    r = json.loads(request(srv, "/api/mics")[1])
    assert r == {"options": [["", "Как в системе"], ["USB Mic", "USB Mic"]], "current": ""}
    assert json.loads(request(srv, "/api/settings", {"key": "mic", "value": "Отключённый"})[1])["ok"]
    assert calls[-1] == ("set_mic", "Отключённый")
    app.recorder.device = "Отключённый"
    assert panel.mic_options(app, [])[-1] == ["Отключённый", "Отключённый"]
