"""Запуск LocalFlow: настройки, движок, модель, клавиша, запись, трей, таблетка.

    python -m localflow

Главный поток — поток окон (значок, меню, таблетка). Модель грузится, клавиша
перехватывается и речь распознаётся в своих потоках.
"""

import ctypes
import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__, core, menu, panel, strings
from .audio import AudioRecorder
from .autotune import START_MODEL, AutoTune
from .core import tr
from .dictation import Dictation
from .engine.catalog import LLAMA_SERVER_EXE, WHISPER_SERVER_EXE
from .keys import KeyboardLogic, chord_from_config, chord_label, normalize
from .paths import APP_DIR, DATA_DIR, ENGINES_DIR, FROZEN, LOG_DIR, MODELS_DIR
from .polisher import TextPolisher
from .transcriber import Transcriber

log = logging.getLogger("localflow")

TRAY_REFRESH_SEC = 0.3
MUTEX_NAME = "Local\\LocalFlow-single-instance"
MAIN_WINDOW_CLASS = "LocalFlowMain"
COMMAND_MESSAGE = "LocalFlow-Command"
COMMANDS = {"settings": 1, "quit": 2}


def setup_logging(console: bool) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
    # Журнал только в файл UTF-8: у программы без консоли вывода нет вовсе,
    # а консоль Windows падает на кириллице
    fh = RotatingFileHandler(LOG_DIR / "localflow.log", maxBytes=1_000_000,
                             backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    log.setLevel(logging.INFO)
    if console and sys.stderr is not None:
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        log.addHandler(sh)


def single_instance() -> bool:
    """Вторая копия программы перехватывала бы ту же клавишу дважды."""
    if os.name != "nt":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    single_instance.handle = handle   # держим до выхода
    return ctypes.get_last_error() != 183   # ERROR_ALREADY_EXISTS


def engine_exe() -> Path:
    env = os.environ.get("LOCALFLOW_ENGINE_EXE")
    if env:
        return Path(env)
    bundled = APP_DIR / "engine" / WHISPER_SERVER_EXE
    if FROZEN or bundled.exists():
        return bundled
    return ENGINES_DIR / "whisper" / WHISPER_SERVER_EXE


def llama_exe() -> Path:
    env = os.environ.get("LOCALFLOW_LLAMA_EXE")
    if env:
        return Path(env)
    bundled = APP_DIR / "engine" / "llama" / LLAMA_SERVER_EXE
    if FROZEN or bundled.exists():
        return bundled
    return ENGINES_DIR / "llama" / LLAMA_SERVER_EXE


def send_command(name: str, wait_exit: float = 0.0) -> bool:
    """Команда уже запущенной копии (второй запуск, установщик).
    False — запущенной копии нет."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.restype = ctypes.c_void_p
    user32.FindWindowW.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p)
    user32.RegisterWindowMessageW.argtypes = (ctypes.c_wchar_p,)
    user32.PostMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)
    user32.GetWindowThreadProcessId.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
    user32.IsWindow.argtypes = (ctypes.c_void_p,)
    hwnd = user32.FindWindowW(MAIN_WINDOW_CLASS, None)
    if not hwnd:
        return False
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    user32.AllowSetForegroundWindow(pid.value)    # пусть панель откроется поверх
    user32.PostMessageW(hwnd, user32.RegisterWindowMessageW(COMMAND_MESSAGE), COMMANDS[name], 0)
    end = time.monotonic() + wait_exit
    while time.monotonic() < end and user32.IsWindow(hwnd):
        time.sleep(0.1)
    return True


GPU_CHECK_VERSION = 2


def migrate_gpu_check(cfg: dict) -> bool:
    """В 0.1.0–0.1.1 видеокарту сравнивали по первому прогону, в который
    входит и подготовка видеокарты, — и зря отказывались от неё. Такой
    замер проводим заново. Отказ после падения движка не трогаем."""
    if cfg.get("gpu_check_version", 1) >= GPU_CHECK_VERSION:
        return False
    cfg["gpu_check_version"] = GPU_CHECK_VERSION
    if cfg.get("gpu_checked") and cfg.get("use_gpu") is False and not cfg.get("gpu_crashed"):
        cfg["gpu_checked"] = False
        cfg["use_gpu"] = True
        cfg.pop("llm_gpu", None)
        log.info("Видеокарту проверю заново: прошлый замер был нечестным")
    return True


class App:
    def __init__(self):
        from .win import keyhook, media, paste, system

        strings.install()
        self.cfg = core.load_config()
        core._UI_LANG = (self.cfg.get("ui_lang") if self.cfg.get("ui_lang") in core.UI_STRINGS
                         else system.system_ui_lang())
        core.ensure_dictionary()
        core.ensure_snippets()

        self.transcriber = Transcriber(engine_exe(), MODELS_DIR, LOG_DIR)
        self.transcriber.language = (self.cfg.get("language")
                                     if self.cfg.get("language") in core.LANGUAGES
                                     else core.DEFAULT_LANGUAGE)
        if migrate_gpu_check(self.cfg):
            core.save_config(self.cfg)
        self.transcriber.use_gpu = bool(self.cfg.get("use_gpu", True))
        self.transcriber.on_gpu_disabled = self._gpu_disabled

        self.polisher = TextPolisher(llama_exe(), MODELS_DIR, LOG_DIR)
        self.polisher.mode = (self.cfg.get("llm_mode") if self.cfg.get("llm_mode") in core.LLM_MODES
                              else core.DEFAULT_LLM_MODE)
        self.polisher.on_gpu_disabled = lambda: self._set("llm_gpu", False)
        self.media = media.MediaPause()
        self.media.enabled = bool(self.cfg.get("pause_media", True))

        self.recorder = AudioRecorder()
        self.recorder.device = self.cfg.get("mic_device") or None
        self.recorder.avoid_apis = {a for a in self.cfg.get("mic_avoid_apis", []) if isinstance(a, str)}
        self.recorder.on_avoid_change = lambda apis: self._set("mic_avoid_apis", apis)

        self.keys = KeyboardLogic(chord_from_config(self.cfg.get("hotkey")),
                                  is_down=keyhook.is_key_down)
        self.sounds = system.Sounds(DATA_DIR / "sounds")
        self.sounds.enabled = bool(self.cfg.get("sounds", True))
        self.paster = paste.Paster()

        autodict = core.AutoDictionary()
        history = core.load_history()
        autodict.bind(lambda: history)
        self.dictation = Dictation(
            self.transcriber, self.recorder, self.keys,
            paste=self.paster, sounds=self.sounds,
            frontmost_app=system.frontmost_app_name,
            window_title=system.frontmost_window_title,
            notify=self.notify, autodict=autodict, history=history,
            polisher=self.polisher, media=self.media)
        d = self.dictation
        d.paste_method = self.cfg.get("paste_method") if self.cfg.get("paste_method") in ("type", "clipboard") else "clipboard"
        d.style = self.cfg.get("style") if self.cfg.get("style") in core.STYLES else core.DEFAULT_STYLE
        d.translate_to = (self.cfg.get("translate_to") if self.cfg.get("translate_to") in core.TRANSLATE_TARGETS
                          else core.DEFAULT_TRANSLATE_TO)
        d.voice_structure = bool(self.cfg.get("voice_structure", True))
        d.voice_edit = bool(self.cfg.get("voice_edit", True))
        d.app_profiles = self.cfg.get("app_profiles", {})
        d.idle_unload_min = (self.cfg.get("idle_unload_min") if self.cfg.get("idle_unload_min") in core.IDLE_UNLOAD_OPTIONS
                             else core.DEFAULT_IDLE_UNLOAD_MIN)

        self.autotune = AutoTune(self.transcriber, self.cfg, core.save_config,
                                 is_idle=lambda: not (d._busy or self.recorder.is_recording))
        self.autotune.on_error = lambda name: self.notify(
            tr("error_title"), tr("model_failed").format(model=core.MODEL_LABELS.get(name, name)))
        self.hook = keyhook.KeyHook(self.keys, d.on_key_event)
        self.pill_animation = (self.cfg.get("pill_animation") if self.cfg.get("pill_animation") in core.PILL_ANIMATIONS
                               else core.DEFAULT_PILL_ANIMATION)

        self.paused = False
        self.started = False          # модель хоть раз запустилась
        self.download = None          # (модель, скачано, всего) при первом запуске
        self.hook_error = None
        self.ui = None
        self.tray = None
        self.pill = None
        self.panel_server = None
        self.panel_window = None
        self._ui_thread = None
        self._shut = False

    # --- Уведомления и значок ------------------------------------------------

    def notify(self, title: str, message: str) -> None:
        if self.ui is None or self.tray is None:
            log.info("%s: %s", title, message)
            return
        self.ui.post(lambda: self.tray.notify(title, message))

    def _refresh_tray(self) -> None:
        self.tray.update(menu.tray_state(self), menu.tooltip(self))

    # --- Модель ----------------------------------------------------------------

    def _gpu_disabled(self) -> None:
        self.cfg["use_gpu"] = False
        self.cfg["gpu_crashed"] = True
        core.save_config(self.cfg)

    def _load_model(self) -> None:
        def progress(have, total):
            self.download = (START_MODEL, have, total)

        try:
            name = self.autotune.ensure_start_model(progress=progress)
        except Exception as exc:
            log.error("Модель не скачалась: %s", exc)
            self.transcriber.load_error = str(exc)
            return
        finally:
            self.download = None
        self.transcriber.model_name = name
        if self.transcriber.load(name):
            self.started = True
            log.info("Готово: зажми %s и говори", chord_label(self.keys.hotkey))
            if not self.cfg.get("welcomed"):
                self.cfg["welcomed"] = True
                core.save_config(self.cfg)
                self.notify(tr("ready_title"),
                            tr("ready_msg").format(key=menu.pretty_key(self.keys.hotkey)))
            if self.polisher.enabled:
                # после Whisper: вдвоём при запуске они толкались бы за диск и память
                self._start_polisher(self.polisher.mode)
            threading.Thread(target=self.dictation.recover_lost_dictation,
                             daemon=True, name="recovery").start()
            self.autotune.after_load()

    def _idle_loop(self) -> None:
        while True:
            time.sleep(30)
            try:
                self.dictation.maybe_unload_idle()
            except Exception as exc:
                log.error("Проверка простоя упала: %s", exc)

    # --- Пункты меню -----------------------------------------------------------

    def _set(self, key: str, value) -> None:
        self.cfg[key] = value
        core.save_config(self.cfg)
        log.info("Настройка %s = %r", key, value)

    def choose_model(self, name: str | None) -> None:
        self.autotune.choose(name)

    def set_language(self, code: str) -> None:
        self.transcriber.language = code
        self._set("language", code)

    def set_style(self, key: str) -> None:
        self.dictation.style = key
        self._set("style", key)

    def set_translate(self, code: str) -> None:
        self.dictation.translate_to = code
        self._set("translate_to", code)
        if code != "off" and self.pill is not None:
            # как на Mac: коротко показать, что режим включился
            self.pill.show_text(f"{tr('translating')} → {code.upper()}", glow="blue")
            self.pill.hide(after=2.0)

    def set_llm_mode(self, mode: str) -> None:
        p = self.polisher
        if mode not in core.LLM_MODES:
            return
        if mode == p.mode and (mode == "off" or p.ready or p.is_loading):
            return        # уже работает; иначе — повторный выбор пробует снова
        self._set("llm_mode", mode)
        if mode == "off":
            self.polisher.set_mode("off")
            return
        self.notify(tr("llm"), tr("llm_loading"))
        self._start_polisher(mode, on_done=lambda ok: self.notify(
            tr("llm"), tr("llm_ready") if ok else tr("llm_failed")))

    def _start_polisher(self, mode: str, on_done=None) -> None:
        # Видеокарта — как решил замер распознавания: встроенная графика,
        # которая медленнее процессора там, медленнее и здесь
        self.polisher.use_gpu = bool(self.cfg.get("llm_gpu", self.cfg.get("use_gpu", True)))
        def done(ok):
            # модель только что загрузилась (бывает, после долгого скачивания) —
            # счётчик простоя с нуля, иначе её тут же выгрузит
            self.dictation.touch()
            if on_done:
                on_done(ok)
        self.polisher.set_mode(mode, on_done=done)

    def set_paste(self, method: str) -> None:
        self.dictation.paste_method = method
        self._set("paste_method", method)

    def set_ui_lang(self, code: str) -> None:
        core._UI_LANG = code
        self._set("ui_lang", code)
        if self.tray is not None:
            self._refresh_tray()

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self.hook.paused = self.paused
        if self.paused:
            self.dictation._cancel_recording("пауза", quiet=True)
            self.keys.enter_armed = False
        else:
            self.keys.set_hotkey(self.keys.hotkey)   # забыть зажатое до паузы
        log.info("Пауза %s", "включена" if self.paused else "выключена")

    def paste_from_history(self, text: str) -> None:
        # фокус уже вернулся в прежнее окно; даём ему секунду прийти в себя
        method = self.dictation.paste_method
        t = threading.Timer(0.35, lambda: self.paster.paste_text(text, method))
        t.daemon = True
        t.start()

    # --- Панель ---------------------------------------------------------------

    def call_ui(self, fn):
        """Выполнить в потоке окон и дождаться: так настройки из панели и из
        меню трея никогда не меняются одновременно."""
        if self.ui is None or threading.get_ident() == self._ui_thread:
            return fn()
        box, done = {}, threading.Event()

        def run():
            try:
                box["value"] = fn()
            except Exception as exc:
                box["error"] = exc
            finally:
                done.set()

        self.ui.post(run)
        if not done.wait(10):
            raise TimeoutError("поток окон не ответил")
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def open_url(self, url: str) -> None:
        """Ссылка — в обычном браузере человека."""
        try:
            os.startfile(url)
        except Exception as exc:
            log.warning("Не открылась ссылка %s: %s", url, exc)

    def open_panel(self) -> None:
        from .win import panel_window

        if self.panel_server is None:
            self.panel_server = panel.PanelServer(self, call=self.call_ui)
        if not self.panel_server.start():
            return
        if self.panel_window is None:
            self.panel_window = panel_window.PanelWindow(DATA_DIR / "panel")
        try:
            self.panel_window.open(self.panel_server.url + "#settings")
        except Exception as exc:
            log.error("Панель не открылась: %s", exc)

    def list_mics(self) -> list[dict]:
        return self.recorder.input_devices()

    def set_hotkey(self, chord) -> None:
        chord = normalize(chord)
        self.keys.set_hotkey(chord)
        self._set("hotkey", list(chord))

    def set_mic(self, name: str | None) -> None:
        self.recorder.device = name
        self._set("mic_device", name or "")
        threading.Thread(target=self.recorder.warm_up, daemon=True, name="mic-warmup").start()

    def set_animation(self, name: str) -> None:
        self.pill_animation = name
        if self.pill is not None:
            self.pill.set_animation(name)
        self._set("pill_animation", name)

    def set_sounds(self, on: bool) -> None:
        self.sounds.enabled = bool(on)
        self._set("sounds", bool(on))

    def set_media_pause(self, on: bool) -> None:
        self.media.enabled = bool(on)
        self._set("pause_media", bool(on))

    def set_idle_unload(self, minutes: int) -> None:
        self.dictation.idle_unload_min = minutes
        self._set("idle_unload_min", minutes)

    def autostart_enabled(self) -> bool:
        from .win import autostart
        return autostart.is_enabled()

    def set_autostart(self, on: bool) -> bool:
        from .win import autostart
        return autostart.set_enabled(on)

    def set_profiles(self, profiles: dict) -> None:
        self.dictation.app_profiles = profiles
        self._set("app_profiles", profiles)

    def _history_changed(self) -> None:
        core.save_history(self.dictation.history)
        if self.dictation.autodict is not None:
            self.dictation.autodict.invalidate()

    def delete_history(self, ts) -> None:
        h = self.dictation.history
        h[:] = [it for it in h if it.get("ts") != ts]     # тот же список: его читает автословарь
        self._history_changed()

    def clear_history(self) -> None:
        self.dictation.history.clear()
        self._history_changed()

    def _on_command(self, wparam, lparam):
        log.info("Команда от второго запуска: %s", wparam)
        if wparam == COMMANDS["settings"]:
            self.open_panel()
        elif wparam == COMMANDS["quit"]:
            self.quit()
        return 0

    def quit(self) -> None:
        log.info("Выход")
        if self.ui is not None:
            self.ui.quit()

    # --- Жизненный цикл --------------------------------------------------------

    def _shutdown(self) -> None:
        if self._shut:
            return
        self._shut = True
        try:
            if self.panel_window is not None:
                self.panel_window.close()
            if self.panel_server is not None:
                self.panel_server.stop()
            if self.tray is not None:
                self.tray.remove()
            if self.pill is not None:
                self.pill.destroy()
        except Exception as exc:
            log.warning("Окна не закрылись: %s", exc)
        self.hook.stop()
        self.media.resume()
        self.media.wait(1.0)         # музыку, поставленную на паузу, вернуть до выхода
        self.polisher.unload()
        self.transcriber.unload()

    def run(self) -> None:
        from .win import overlay, tray, ui, w32

        log.info("LocalFlow %s для Windows запускается", __version__)
        self.ui = ui.UiLoop()
        self._ui_thread = threading.get_ident()
        self.pill = overlay.PillWindow(self.ui, self.pill_animation)
        self.dictation.indicator = self.pill
        self.tray = tray.Tray(self.ui, lambda: menu.build(self))
        self.tray.add(menu.tray_state(self), menu.tooltip(self))
        self.ui.every(TRAY_REFRESH_SEC, self._refresh_tray)
        self.ui.on(w32.WM_ENDSESSION, lambda wp, lp: self._shutdown() if wp else None)
        self.ui.on(w32.user32.RegisterWindowMessageW(COMMAND_MESSAGE), self._on_command)

        if not self.hook.start():
            self.hook_error = self.hook.error
            self.notify(tr("error_title"), tr("st_hook_error"))
        threading.Thread(target=self.recorder.warm_up, daemon=True, name="mic-warmup").start()
        self.media.warm_up()
        threading.Thread(target=self._load_model, daemon=True, name="model-loader").start()
        threading.Thread(target=self._idle_loop, daemon=True, name="idle").start()
        self.pill.prewarm()
        if FROZEN and not self.cfg.get("intro_shown"):
            # первый запуск после установки: сразу видно клавишу и загрузку модели
            self.cfg["intro_shown"] = True
            core.save_config(self.cfg)
            self.ui.after(0.5, self.open_panel)

        # Ctrl+C в консоли: цикл окон спит в Windows и сам его не заметит
        self._console_handler = w32.HANDLER_ROUTINE(lambda ev: bool(self.quit() or True))
        w32.kernel32.SetConsoleCtrlHandler(self._console_handler, True)
        try:
            self.ui.run()
        finally:
            w32.kernel32.SetConsoleCtrlHandler(self._console_handler, False)
            self._shutdown()


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if "--quit" in argv:
        # установщик закрывает программу перед обновлением и удалением
        send_command("quit", wait_exit=15)
        return
    setup_logging(console=not FROZEN)
    if not single_instance():
        log.info("LocalFlow уже запущен — открываю настройки")
        send_command("settings")
        return
    from .win import w32
    w32.set_dpi_aware()
    try:
        App().run()
    except Exception:
        log.exception("LocalFlow упал")
        raise
