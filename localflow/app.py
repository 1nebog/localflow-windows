"""Запуск LocalFlow: настройки, движок, модель, клавиша, запись.

Пока без значка в трее и таблетки — они следующим этапом. Сейчас программа
запускается из консоли и пишет, что делает, в журнал:

    python -m localflow
"""

import ctypes
import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__, core, strings
from .audio import AudioRecorder
from .autotune import AutoTune
from .dictation import Dictation
from .engine.catalog import WHISPER_SERVER_EXE
from .keys import KeyboardLogic, chord_from_config, chord_label
from .paths import DATA_DIR, ENGINES_DIR, LOG_DIR, MODELS_DIR
from .transcriber import Transcriber

log = logging.getLogger("localflow")


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
    handle = kernel32.CreateMutexW(None, False, "Local\\LocalFlow-single-instance")
    single_instance.handle = handle   # держим до выхода
    return ctypes.get_last_error() != 183   # ERROR_ALREADY_EXISTS


def engine_exe() -> Path:
    env = os.environ.get("LOCALFLOW_ENGINE_EXE")
    return Path(env) if env else ENGINES_DIR / "whisper" / WHISPER_SERVER_EXE


class App:
    def __init__(self):
        from .win import keyhook, paste, system

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
        self.transcriber.use_gpu = bool(self.cfg.get("use_gpu", True))
        self.transcriber.on_gpu_disabled = self._gpu_disabled

        self.recorder = AudioRecorder()
        self.recorder.device = self.cfg.get("mic_device") or None

        self.keys = KeyboardLogic(chord_from_config(self.cfg.get("hotkey")),
                                  is_down=keyhook.is_key_down)
        self.sounds = system.Sounds(DATA_DIR / "sounds")
        self.sounds.enabled = bool(self.cfg.get("sounds", True))

        autodict = core.AutoDictionary()
        history = core.load_history()
        autodict.bind(lambda: history)
        self.dictation = Dictation(
            self.transcriber, self.recorder, self.keys,
            paste=paste.Paster(), sounds=self.sounds,
            frontmost_app=system.frontmost_app_name,
            window_title=system.frontmost_window_title,
            autodict=autodict, history=history)
        d = self.dictation
        d.paste_method = self.cfg.get("paste_method") if self.cfg.get("paste_method") in ("type", "clipboard") else "clipboard"
        d.style = self.cfg.get("style") if self.cfg.get("style") in core.STYLES else core.DEFAULT_STYLE
        d.translate_to = (self.cfg.get("translate_to") if self.cfg.get("translate_to") in core.TRANSLATE_TARGETS
                          else core.DEFAULT_TRANSLATE_TO)
        d.voice_structure = bool(self.cfg.get("voice_structure", True))
        d.app_profiles = self.cfg.get("app_profiles", {})
        d.idle_unload_min = (self.cfg.get("idle_unload_min") if self.cfg.get("idle_unload_min") in core.IDLE_UNLOAD_OPTIONS
                             else core.DEFAULT_IDLE_UNLOAD_MIN)

        self.autotune = AutoTune(self.transcriber, self.cfg, core.save_config,
                                 is_idle=lambda: not (d._busy or self.recorder.is_recording))
        self.hook = keyhook.KeyHook(self.keys, d.on_key_event)

    def _gpu_disabled(self) -> None:
        self.cfg["use_gpu"] = False
        core.save_config(self.cfg)

    def _load_model(self) -> None:
        try:
            name = self.autotune.ensure_start_model()
        except Exception as exc:
            log.error("Модель не скачалась: %s", exc)
            self.transcriber.load_error = str(exc)
            return
        self.transcriber.model_name = name
        if self.transcriber.load(name):
            log.info("Готово: зажми %s и говори", chord_label(self.keys.hotkey))
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

    def run(self) -> None:
        log.info("LocalFlow %s для Windows запускается", __version__)
        if not self.hook.start():
            raise SystemExit(f"Перехват клавиатуры не включился: {self.hook.error}")
        threading.Thread(target=self.recorder.warm_up, daemon=True, name="mic-warmup").start()
        threading.Thread(target=self._load_model, daemon=True, name="model-loader").start()
        threading.Thread(target=self._idle_loop, daemon=True, name="idle").start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("Выход")
        finally:
            self.hook.stop()
            self.transcriber.unload()


def main() -> None:
    setup_logging(console=True)
    if not single_instance():
        log.info("LocalFlow уже запущен")
        return
    App().run()
