"""Автоподбор модели и видеокарты без настоящего движка."""

from pathlib import Path

from localflow.autotune import AutoTune
from localflow.engine.catalog import WHISPER_MODELS

# сколько «компьютер» думает над фразой: (модель, видеокарта?) -> секунды
SPEED = {}


def fake_download(model, folder, progress=None, **kw):
    path = Path(folder) / model.file
    with open(path, "wb") as f:
        f.truncate(model.size)   # пустой файл нужного размера
    return path


class FakeTr:
    def __init__(self, folder, device="GPU"):
        self.models_dir = Path(folder)
        self.model_name = "base"
        self.use_gpu = True
        self.gpu_name = device
        self.warmup_sec = None
        self.loads = []

    @property
    def device(self):
        return self.gpu_name if self.use_gpu else "CPU"

    def load(self, name=None):
        self.model_name = name or self.model_name
        self.warmup_sec = SPEED[(self.model_name, self.device != "CPU")]
        self.loads.append((self.model_name, self.device))
        return True

    def unload(self):
        pass


def make(tmp_path, device="GPU", cfg=None):
    fake_download(WHISPER_MODELS["base"], tmp_path)
    tr = FakeTr(tmp_path, device)
    cfg = {} if cfg is None else cfg
    saved = []
    at = AutoTune(tr, cfg, lambda c: saved.append(dict(c)), downloader=fake_download)
    tr.load("base")
    return at, tr, cfg


def test_gaming_gpu_goes_to_turbo(tmp_path):
    SPEED.clear()
    SPEED.update({("base", True): 0.15, ("base", False): 1.0,
                  ("large-v3-turbo", True): 1.2})
    at, tr, cfg = make(tmp_path)
    at._tune()
    assert tr.model_name == "large-v3-turbo" and tr.use_gpu
    assert cfg["use_gpu"] is True and cfg["model"] == "large-v3-turbo"


def test_integrated_gpu_slower_than_cpu_switched_off(tmp_path):
    SPEED.clear()
    SPEED.update({("base", True): 1.4, ("base", False): 0.9,
                  ("small", False): 2.4})
    at, tr, cfg = make(tmp_path, device="Intel UHD")
    at._tune()
    assert cfg["use_gpu"] is False and not tr.use_gpu
    # base на процессоре 0.9 c: small по прикидке 3.2 c — не тянет
    assert tr.model_name == "base"


def test_turbo_too_slow_in_reality_steps_down_and_is_remembered(tmp_path):
    SPEED.clear()
    SPEED.update({("base", True): 0.2, ("large-v3-turbo", True): 6.0,
                  ("small", True): 1.1})
    at, tr, cfg = make(tmp_path, cfg={"gpu_checked": True, "use_gpu": True})
    at._tune()
    assert tr.model_name == "small"
    assert cfg["models_too_slow"] == ["large-v3-turbo"]
    at._tune()                         # следующий запуск не лезет в turbo снова
    assert tr.model_name == "small"


def test_manual_choice_is_respected(tmp_path):
    SPEED.clear()
    SPEED.update({("base", False): 0.1, ("base", True): 0.1})
    at, tr, cfg = make(tmp_path, device="CPU", cfg={"model_auto": False})
    at._tune()
    assert tr.model_name == "base" and len(tr.loads) == 1


def test_startup_model_prefers_choice_then_best_downloaded(tmp_path):
    SPEED.clear()
    SPEED[("base", True)] = 0.5
    at, tr, cfg = make(tmp_path)
    assert at.startup_model() == "base"
    fake_download(WHISPER_MODELS["small"], tmp_path)
    assert at.startup_model() == "small"
    cfg["model"] = "base"
    assert at.startup_model() == "base"
