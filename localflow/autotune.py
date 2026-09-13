"""Автоподбор модели и видеокарты под этот компьютер.

Первый запуск: программа работает на лёгкой base (скачивается сразу, 60 МБ),
замеряет скорость и в фоне переходит на самую точную модель, которую
компьютер тянет без долгого ожидания. Встроенная графика бывает медленнее
процессора — это тоже проверяется один раз, и выбор запоминается.

Если человек сам выбрал модель в настройках, автоподбор её не трогает.
"""

import logging
import threading
import time

from .engine import tiers
from .engine.catalog import WHISPER_MODELS
from .engine.download import download, is_ready

log = logging.getLogger("localflow")

START_MODEL = "base"


class AutoTune:
    def __init__(self, transcriber, cfg: dict, save_cfg, is_idle=lambda: True,
                 downloader=download):
        self.tr = transcriber
        self.cfg = cfg
        self._save = save_cfg
        self._is_idle = is_idle      # не переключаем модель посреди диктовки
        self._download = downloader
        self._busy = threading.Lock()
        self.status = ""             # для настроек: что сейчас происходит
        self.progress = None         # (модель, скачано, всего) пока качаем
        self.on_error = None         # колбэк(модель): выбранная модель не запустилась

    @property
    def auto(self) -> bool:
        return self.cfg.get("model_auto", True)

    def downloaded(self) -> list[str]:
        return [m for m in WHISPER_MODELS if is_ready(WHISPER_MODELS[m], self.tr.models_dir)]

    def startup_model(self) -> str | None:
        """С какой модели стартовать: выбранная, если скачана; иначе самая
        точная из скачанных; None — ничего нет, надо качать."""
        have = self.downloaded()
        chosen = self.cfg.get("model")
        if chosen in have:
            return chosen
        for m in reversed(tiers.LADDER):
            if m in have:
                return m
        return have[0] if have else None

    def ensure_start_model(self, progress=None) -> str:
        name = self.startup_model()
        if name:
            return name
        log.info("Моделей нет — скачиваю %s", START_MODEL)
        self._download(WHISPER_MODELS[START_MODEL], self.tr.models_dir, progress=progress)
        return START_MODEL

    # --- После загрузки модели -------------------------------------------

    def after_load(self) -> None:
        """Зовётся, когда модель запустилась и прогрелась. Работает в фоне."""
        threading.Thread(target=self._tune, daemon=True, name="autotune").start()

    def _wait_idle(self) -> None:
        while not self._is_idle():
            time.sleep(0.5)

    def _load(self, name: str) -> bool:
        self._wait_idle()
        ok = self.tr.load(name)
        if ok:
            self.cfg["model"] = name
            self._save(self.cfg)
        return ok

    def _check_device(self) -> None:
        """Один раз: не медленнее ли видеокарта процессора."""
        if self.cfg.get("gpu_checked") or not self.tr.use_gpu:
            return
        if (self.tr.device or "CPU") == "CPU":
            self.cfg["gpu_checked"] = True    # видеокарты нет — и проверять нечего
            self._save(self.cfg)
            return
        gpu_sec = self.tr.warmup_sec
        self._wait_idle()
        self.tr.unload()
        self.tr.use_gpu = False
        cpu_ok = self.tr.load()
        cpu_sec = self.tr.warmup_sec if cpu_ok else None
        use_gpu = tiers.prefer_gpu(gpu_sec, cpu_sec)
        log.info("Видеокарта %s: %.2f c, процессор: %s c — считаю на %s",
                 self.tr.device or "?", gpu_sec or 0,
                 f"{cpu_sec:.2f}" if cpu_sec else "?", "видеокарте" if use_gpu else "процессоре")
        self.cfg["gpu_checked"] = True
        self.cfg["use_gpu"] = use_gpu
        self._save(self.cfg)
        if use_gpu:
            self._wait_idle()
            self.tr.unload()
            self.tr.use_gpu = True
            self.tr.load()

    def _tune(self) -> None:
        if not self._busy.acquire(blocking=False):
            return
        try:
            self._check_device()
            if not self.auto:
                return
            too_slow = set(self.cfg.get("models_too_slow", []))
            for _ in range(len(tiers.LADDER)):   # предохранитель от качелей
                current, sec = self.tr.model_name, self.tr.warmup_sec
                if sec is None:
                    return
                on_gpu = (self.tr.device or "CPU") != "CPU"
                want = tiers.recommend(current, sec, on_gpu)
                if want == current:
                    self.status = ""
                    return
                up = tiers.LADDER.index(want) > tiers.LADDER.index(current)
                if up and want in too_slow:
                    return
                log.info("Модель %s думает %.2f c на фразу — перехожу на %s",
                         current, sec, want)
                if not up:
                    too_slow.add(current)
                    self.cfg["models_too_slow"] = sorted(too_slow)
                if not is_ready(WHISPER_MODELS[want], self.tr.models_dir):
                    self.status = f"download:{want}"
                    self._fetch(want)
                self.status = f"switch:{want}"
                if not self._load(want):
                    self._load(current)
                    return
        except Exception as exc:
            log.warning("Автоподбор модели не удался: %s", exc)
        finally:
            self.status = ""
            self._busy.release()

    def _fetch(self, name: str) -> None:
        model = WHISPER_MODELS[name]
        self.progress = (name, 0, model.size)
        try:
            self._download(model, self.tr.models_dir,
                           progress=lambda have, total: setattr(self, "progress", (name, have, total)))
        finally:
            self.progress = None

    # --- Выбор в меню ---------------------------------------------------------

    def choose(self, name: str | None) -> threading.Thread:
        """None — снова автоподбор; иначе эта модель, и автоподбор её не трогает."""
        if name is None:
            self.cfg["model_auto"] = True
            self._save(self.cfg)
            log.info("Модель: автоподбор")
            return self._start(self._tune, "autotune")
        self.cfg["model_auto"] = False
        self.cfg["model"] = name
        self._save(self.cfg)
        log.info("Модель выбрана вручную: %s", name)
        return self._start(lambda: self._switch(name), "model-switch")

    @staticmethod
    def _start(target, name) -> threading.Thread:
        t = threading.Thread(target=target, daemon=True, name=name)
        t.start()
        return t

    def _switch(self, name: str) -> None:
        with self._busy:                  # дождаться автоподбора, если он идёт
            previous = self.tr.model_name
            try:
                if not is_ready(WHISPER_MODELS[name], self.tr.models_dir):
                    self.status = f"download:{name}"
                    self._fetch(name)
                if name == previous and self.tr.is_ready:
                    return
                self.status = f"switch:{name}"
                if not self._load(name):
                    raise RuntimeError(self.tr.load_error or "модель не запустилась")
            except Exception as exc:
                log.error("Модель %s не запустилась: %s", name, exc)
                if previous and previous != name:
                    self.cfg["model"] = previous
                    self._save(self.cfg)
                    if not self.tr.is_ready:
                        self._load(previous)
                if self.on_error:
                    self.on_error(name)
            finally:
                self.status = ""
