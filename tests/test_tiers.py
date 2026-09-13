"""Подбор модели под скорость компьютера."""

from localflow.engine.tiers import live_checks_ok, prefer_gpu, recommend


def test_slow_cpu_gets_light_model():
    # как процессор раннера GitHub: base считает фразу ~2 c
    assert recommend("base", 2.0, on_gpu=False) == "base"
    # совсем «калькулятор»: base думает 4 c — берём tiny
    assert recommend("base", 4.0, on_gpu=False) == "tiny"


def test_fast_cpu_gets_small_not_turbo():
    # Mac на 4 потоках: base 0.25 c; turbo по прикидке 4 c — не тянет
    assert recommend("base", 0.25, on_gpu=False) == "small"


def test_gpu_gets_turbo():
    # игровая видеокарта: base за 0.2 c
    assert recommend("base", 0.2, on_gpu=True) == "large-v3-turbo"


def test_turbo_too_slow_steps_down():
    # после перехода turbo на деле думает 6 c — на ступень вниз
    assert recommend("large-v3-turbo", 6.0, on_gpu=True) == "small"


def test_current_model_kept_within_margin():
    # turbo чуть медленнее цели, но терпимо — не качаем туда-сюда
    assert recommend("large-v3-turbo", 3.0, on_gpu=True) == "large-v3-turbo"


def test_integrated_gpu_slower_than_cpu_is_skipped():
    assert prefer_gpu(0.5, 0.52) is False
    assert prefer_gpu(0.15, 0.6) is True
    assert prefer_gpu(None, 0.6) is False


def test_live_voice_cancel_only_on_fast_engine():
    assert live_checks_ok(0.3)
    assert not live_checks_ok(2.0)
    assert not live_checks_ok(None)
