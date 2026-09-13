"""Таблетка без Windows: картинка, анимации, вызовы из разных потоков."""

import numpy as np
import pytest

from localflow import pill
from localflow.icons import STATES, tray_icon
from localflow.pill import N_BARS, PillController, PillModel, PillRenderer


def fake_text(text, px):
    """Текст как сплошной прямоугольник: ширина по числу букв."""
    return np.ones((round(px * 1.3), round(len(text) * px * 0.55)), np.float32)


def alpha(buf, y, x):
    return buf[y, x, 3]


@pytest.mark.parametrize("scale", [1.0, 1.25, 2.0])
def test_frame_size_follows_screen_scale(scale):
    r = PillRenderer(scale)
    m = PillModel()
    m.show_text("Говорите…", False, "blue", 0.0)
    buf = r.render(m, 1.0, fake_text)
    assert buf.shape == (r.height, r.width, 4) and buf.dtype == np.uint8
    assert r.w == round(pill.WIDTH * scale) and r.pad == round(pill.GLOW_PAD * scale)


def test_capsule_dark_glow_colored_corners_empty():
    r = PillRenderer(1.0)
    m = PillModel()
    m.show_wave([0.0] * N_BARS, False, "red", 0.0)
    buf = r.render(m, 1.0, fake_text)
    cy = r.height // 2
    # середина капсулы между столбиками — тёмная и почти непрозрачная
    edge = r.pad + 6
    assert alpha(buf, cy, edge) > 220 and buf[cy, edge, :3].max() < 40
    # свечение сразу за краем капсулы — красное
    b, g, rr, a = buf[cy, r.pad - 4]
    assert a > 20 and rr > g and rr > b
    # углы окна пустые: свечение не упирается в край
    assert alpha(buf, 0, 0) == 0 and alpha(buf, -1, -1) == 0
    # цвета предумножены: канал не ярче прозрачности
    assert (buf[..., :3].max(axis=2) <= buf[..., 3]).all()


def test_wave_bars_grow_with_level():
    r = PillRenderer(1.0)
    m = PillModel()
    m.show_wave([1.0] * N_BARS, False, "blue", 0.0)
    loud = r.render(m, 2.0, fake_text)[:, :, 0].copy()   # синий канал (BGRA)
    m2 = PillModel()
    m2.show_wave([0.0] * N_BARS, False, "blue", 0.0)
    quiet = r.render(m2, 2.0, fake_text)[:, :, 0].copy()
    box = (slice(r.pad, r.pad + r.h), slice(r.pad, r.pad + r.w))
    assert (loud[box] > 150).sum() > 4 * (quiet[box] > 150).sum()


def test_text_is_centered_and_shrinks_to_fit():
    r = PillRenderer(1.0)
    seen = []

    def measure(text, px):
        seen.append(px)
        return fake_text(text, px)

    long_text = "Тишина — ничего не вставил, совсем ничего"
    mask = r.text_px(long_text, measure)
    assert seen[0] == pill.TEXT_PX and seen[-1] < pill.TEXT_PX
    assert mask.shape[1] <= r.w - 2 * pill.TEXT_SIDE or seen[-1] == pill.TEXT_MIN_PX


def test_lock_does_not_overlap_text():
    r = PillRenderer(1.0)
    m = PillModel()
    m.show_text("Тишина — ничего не вставил", True, "red", 0.0)
    layer = r._text_layer(m, 1.0, fake_text)
    lock_right = r._lock_pos[0] + r._lock.shape[1]
    cols = np.where(layer.max(axis=0) > 0)[0]
    assert cols.min() > lock_right


def test_text_fades_in_and_rises():
    m = PillModel()
    m.show_text("Распознаю…", False, "green", 10.0)
    r = PillRenderer(1.0)
    early = r._text_layer(m, 10.02, fake_text)
    late = r._text_layer(m, 11.0, fake_text)
    assert early.max() < 0.3 < late.max()


def test_glow_color_fades_smoothly_but_not_on_fresh_show():
    m = PillModel()
    m.show_text("Говорите…", False, "red", 0.0)
    assert m.color(0.0) == pill.COLORS["red"]        # появилась сразу красной
    m.show_text("Говорите…", False, "blue", 1.0)
    mid = m.color(1.7)
    assert mid != pill.COLORS["red"] and mid != pill.COLORS["blue"]
    assert m.color(3.0) == pytest.approx(pill.COLORS["blue"])
    m.hide()
    m.show_wave([0.5] * N_BARS, False, "green", 5.0)
    assert m.color(5.0) == pill.COLORS["green"]


def test_appear_rises_from_below():
    m = PillModel()
    m.show_text("x", False, "blue", 0.0)
    a0, drop0 = m.appear(0.0)
    a1, drop1 = m.appear(1.0)
    assert a0 == 0 and drop0 == pill.APPEAR_RISE
    assert a1 == 1 and drop1 == 0


@pytest.mark.parametrize("name", pill.PILL_ANIMATIONS)
def test_animations_render(name):
    r = PillRenderer(1.5)
    m = PillModel(name)
    m.show_wave(np.linspace(0, 1, N_BARS), True, "blue", 0.0)
    frames = [r.render(m, t, fake_text)[..., 3].astype(int).sum() for t in (0.4, 1.0, 1.7, 2.9)]
    if name == "static":
        assert len(set(frames)) == 1 or max(frames) - min(frames) < 0.01 * max(frames)
    else:
        assert max(frames) != min(frames)


def test_static_animation_stops_frames_when_idle():
    m = PillModel("static")
    m.show_text("Тишина", False, "red", 0.0)
    assert m.animating(0.1)          # идёт появление
    assert not m.animating(5.0)      # красный текст без блика — кадры не нужны
    m.show_text("Распознаю…", False, "green", 5.0)
    assert m.animating(9.0)          # у зелёного бежит блик
    m.hide()
    assert not m.animating(9.0)
    assert PillModel("pulse").animating(0) is False


def test_frame_is_cheap():
    import time
    r = PillRenderer(1.5)
    m = PillModel()
    m.show_wave(np.random.default_rng(0).random(N_BARS), True, "blue", 0.0)
    r.render(m, 0.0, fake_text)
    t0 = time.perf_counter()
    for i in range(30):
        r.render(m, i / 30, fake_text)
    per_frame = (time.perf_counter() - t0) / 30
    assert per_frame < 0.02, f"кадр {per_frame * 1000:.1f} мс"


# --- Вызовы из потоков -------------------------------------------------------

class Surface:
    def __init__(self):
        self.log = []

    def present(self, fresh):
        self.log.append(("present", fresh))

    def withdraw(self):
        self.log.append(("withdraw",))


class ManualTimer:
    pending = []

    def __init__(self, sec, fn):
        self.fn = fn
        self.daemon = False

    def start(self):
        ManualTimer.pending.append(self.fn)


def make_controller():
    queue = []
    surface = Surface()
    ManualTimer.pending = []
    c = PillController(queue.append, surface, clock=lambda: 1.0, timer=ManualTimer)
    return c, queue, surface


def run(queue):
    while queue:
        queue.pop(0)()


def test_calls_run_only_in_window_thread():
    c, queue, surface = make_controller()
    c.show_text("Говорите…")
    assert surface.log == [] and not c.model.visible
    run(queue)
    assert surface.log == [("present", True)] and c.model.visible


def test_wave_frames_are_merged():
    c, queue, surface = make_controller()
    for i in range(5):
        c.show_wave([i / 10] * N_BARS)
    assert len(queue) == 1
    run(queue)
    assert c.model.targets[0] == pytest.approx(0.4)
    assert surface.log == [("present", True)]


def test_late_hide_does_not_close_new_recording():
    c, queue, surface = make_controller()
    c.show_text("Тишина", glow="red")
    c.hide(after=1.2)
    run(queue)
    c.show_text("Говорите…")           # новая запись до срабатывания таймера
    run(queue)
    for fn in ManualTimer.pending:
        fn()
    run(queue)
    assert c.model.visible and ("withdraw",) not in surface.log


def test_hide_after_show():
    c, queue, surface = make_controller()
    c.show_wave([0.5] * N_BARS)
    c.hide()
    run(queue)
    assert not c.model.visible and surface.log[-1] == ("withdraw",)


def test_text_after_wave_drops_pending_wave():
    c, queue, surface = make_controller()
    c.show_wave([0.5] * N_BARS)
    c.show_text("Распознаю…", glow="green")
    run(queue)
    assert c.model.mode == "text" and c.model.glow == "green"


# --- Значок ----------------------------------------------------------------------

@pytest.mark.parametrize("light", [False, True])
def test_tray_icons(light):
    icons = {s: tray_icon(s, 20, light) for s in STATES}
    for img in icons.values():
        assert img.shape == (20, 20, 4) and img[..., 3].max() == 255 or img[..., 3].max() > 100
    idle = icons["idle"]
    fg = idle[..., :3][idle[..., 3] > 200].mean(axis=0)
    assert (fg > 200).all() if not light else (fg < 60).all()
    rec = icons["recording"]
    r, g, b = rec[..., :3][rec[..., 3] > 200].mean(axis=0)
    assert r > 2 * g and r > 2 * b
    assert icons["loading"][..., 3].max() < 140          # бледный
    orange = icons["error"][..., :3][icons["error"][..., 3] > 200]
    assert len(orange) and orange[:, 0].mean() > 200 and orange[:, 2].mean() < 80
