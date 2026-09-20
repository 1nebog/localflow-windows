"""Таблетка — плавающий индикатор записи: вид и движение.

Перенос `RecordingIndicator` версии для Mac: тёмная капсула со свечением по
контуру, живая волна или текст с бегущим бликом, замок при закреплённой
записи. Синее свечение — речь, красное — тишина или отмена, зелёное —
распознавание.

На Mac всё это рисует системный CoreAnimation. У Windows такого нет, поэтому
картинку считаем сами (numpy) и отдаём окну готовыми пикселями. Здесь нет
ничего от Windows — только расчёт кадра, его можно проверить где угодно.
Окно — в `win/overlay.py`.

Чтобы не грузить слабые компьютеры: размытие свечения считается один раз,
в каждом кадре только смешивание готовых масок; скрытая таблетка не
рисуется вовсе.
"""

import math
import threading
import time

import numpy as np

from .core import DEFAULT_PILL_ANIMATION, PILL_ANIMATIONS

N_BARS = 20

# Размеры в точках экрана при масштабе 100% (как на Mac)
WIDTH = 240
HEIGHT = 46
GLOW_PAD = 48         # запас окна вокруг капсулы под свечение
BOTTOM_GAP = 72       # от низа капсулы до панели задач
BAR_W = 4.0
BAR_GAP = 3.0
BAR_MIN_H = 4.0
BAR_MAX_H = 26.0
TEXT_PX = 15          # текст ужимается до TEXT_MIN_PX, если не влезает
TEXT_MIN_PX = 11
TEXT_SIDE = 17        # поля текста с каждой стороны
LOCK_X = 14           # замок от левого края капсулы
TIMER_PX = 11         # часы записи справа в капсуле
TIMER_X = 14          # от правого края капсулы
TIMER_ALPHA = 0.55

GLOW_SIGMA = 11.0     # размытие свечения (shadowRadius 22 на Mac)
CAPSULE_GRAY = 0.07
CAPSULE_ALPHA = 0.92

# Системные цвета Apple: так таблетка выглядит одинаково на обеих системах
COLORS = {
    "blue": (0 / 255, 122 / 255, 255 / 255),
    "red": (255 / 255, 59 / 255, 48 / 255),
    "green": (52 / 255, 199 / 255, 89 / 255),
}
BAR_COLOR = COLORS["blue"]

APPEAR_SEC = 0.32     # проявление с подъёмом снизу
APPEAR_RISE = 16
TEXT_APPEAR_SEC = 0.35
TEXT_RISE = 5
COLOR_FADE_SEC = 1.4
BAR_TAU = 0.07        # столбики догоняют громкость плавно, как на Mac
SHIMMER_SEC = {"green": 1.2, "blue": 2.4}   # у красного блик не бежит


def _ease_out(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1 - (1 - t) ** 3


def _ease_in_out(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 0.5 - 0.5 * math.cos(math.pi * t)


def _wave(t: float, half_period: float) -> float:
    """0 → 1 → 0 плавно, туда и обратно за 2·half_period."""
    return 0.5 - 0.5 * math.cos(math.pi * t / half_period)


# --- Состояние ----------------------------------------------------------------

class PillModel:
    """Что показано и в какой фазе анимации. Только поток окна."""

    def __init__(self, animation: str = DEFAULT_PILL_ANIMATION):
        self.animation = animation if animation in PILL_ANIMATIONS else DEFAULT_PILL_ANIMATION
        self.visible = False
        self.mode = "text"            # "text" | "wave"
        self.text = ""
        self.locked = False
        self.timer = ""               # «0:07» справа: сколько идёт запись
        self.glow = "blue"
        self._color_from = COLORS["blue"]
        self._color_to = COLORS["blue"]
        self._color_t0 = -1e9
        self.appear_t0 = -1e9
        self.text_t0 = -1e9
        self.shimmer_t0 = 0.0
        self.targets = np.zeros(N_BARS, np.float32)
        self.bars = np.zeros(N_BARS, np.float32)
        self._bars_t = 0.0

    def set_animation(self, name: str) -> None:
        if name in PILL_ANIMATIONS:
            self.animation = name

    def _set_glow(self, glow: str, now: float, animate: bool) -> None:
        glow = glow if glow in COLORS else "blue"
        if not animate:
            self._color_from = self._color_to = COLORS[glow]
            self._color_t0 = -1e9
        elif glow != self.glow:
            self._color_from = self.color(now)
            self._color_to = COLORS[glow]
            self._color_t0 = now
        self.glow = glow

    def _appear(self, now: float) -> bool:
        fresh = not self.visible
        if fresh:
            self.visible = True
            self.appear_t0 = now
        return fresh

    def show_text(self, text: str, locked: bool, glow: str, now: float,
                  timer: str = "") -> bool:
        """Возвращает True, если таблетка только что появилась."""
        fresh = self._appear(now)
        self.timer = timer
        if fresh or self.mode != "text" or text != self.text:
            self.text_t0 = now
            self.shimmer_t0 = now
        elif glow != self.glow:
            self.shimmer_t0 = now     # новая скорость блика — с начала
        self._set_glow(glow, now, animate=not fresh)
        self.mode, self.text, self.locked = "text", text, locked
        return fresh

    def show_wave(self, levels, locked: bool, glow: str, now: float,
                  timer: str = "") -> bool:
        fresh = self._appear(now)
        self.timer = timer
        lv = np.zeros(N_BARS, np.float32)
        vals = np.clip(np.asarray(list(levels)[:N_BARS], np.float32), 0.0, 1.0)
        lv[:len(vals)] = vals
        if fresh or self.mode != "wave":
            self.bars[:] = 0.0
            self._bars_t = now
        self.targets = lv
        self._set_glow(glow, now, animate=not fresh)
        self.mode, self.locked = "wave", locked
        return fresh

    def hide(self) -> None:
        self.visible = False

    # --- Что рисовать в момент now ---

    def color(self, now: float) -> tuple:
        t = _ease_in_out((now - self._color_t0) / COLOR_FADE_SEC)
        return tuple(a + (b - a) * t for a, b in zip(self._color_from, self._color_to))

    def glow_params(self, now: float) -> tuple[float, float, tuple[float, float]]:
        """(яркость, размах 0..1, сдвиг сгустка в точках)."""
        t = max(0.0, now - self.appear_t0)
        if self.animation == "static":
            return 0.85, 0.5, (0.0, 0.0)
        if self.animation == "breath":
            k = _wave(t, 2.6)
            return 0.35 + 0.65 * k, k, (0.0, 0.0)
        if self.animation == "orbit":
            a = 2 * math.pi * (t / 2.2)
            return 0.85, 0.5, (10 * math.cos(a), 10 * math.sin(a))
        return 0.45 + 0.55 * _wave(t, 1.3), 0.5, (0.0, 0.0)   # pulse

    def appear(self, now: float) -> tuple[float, float]:
        """(прозрачность окна, насколько ниже своего места)."""
        e = _ease_out((now - self.appear_t0) / APPEAR_SEC)
        return e, APPEAR_RISE * (1 - e)

    def step_bars(self, now: float) -> None:
        dt = max(0.0, now - self._bars_t)
        self._bars_t = now
        k = 1 - math.exp(-dt / BAR_TAU)
        self.bars += (self.targets - self.bars) * k

    def animating(self, now: float) -> bool:
        """Нужны ли кадры по таймеру. Иначе рисуем только по событиям."""
        if not self.visible:
            return False
        if self.animation != "static":
            return True
        if now - self.appear_t0 < APPEAR_SEC or now - self._color_t0 < COLOR_FADE_SEC:
            return True
        if self.mode == "text":
            return self.glow in SHIMMER_SEC or now - self.text_t0 < TEXT_APPEAR_SEC
        return bool(np.abs(self.targets - self.bars).max() > 0.01)


# --- Картинка -----------------------------------------------------------------

def _blur(a: np.ndarray, sigma: float) -> np.ndarray:
    r = max(1, int(3 * sigma))
    x = np.arange(-r, r + 1, dtype=np.float32)
    k = np.exp(-x * x / (2 * sigma * sigma))
    k /= k.sum()
    a = np.apply_along_axis(np.convolve, 0, a, k, mode="same")
    a = np.apply_along_axis(np.convolve, 1, a, k, mode="same")
    return a.astype(np.float32)


def _shift(a: np.ndarray, dx: int, dy: int) -> np.ndarray:
    out = np.zeros_like(a)
    h, w = a.shape
    if abs(dx) >= w or abs(dy) >= h:
        return out
    ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
    xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
    out[yd, xd] = a[ys, xs]
    return out


def _stadium(xx, yy, x0, y0, w, h) -> np.ndarray:
    """Капсула со сглаженным краем: 1 внутри, 0 снаружи."""
    r = min(w, h) / 2
    cx = np.clip(xx, x0 + r, x0 + w - r)
    cy = np.clip(yy, y0 + r, y0 + h - r)
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) - r
    return np.clip(0.5 - d, 0.0, 1.0).astype(np.float32)


def _lock_mask(size: int) -> np.ndarray:
    """Замок: дужка и корпус, без шрифтов с эмодзи."""
    s = size / 14
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32) + 0.5
    body = _rounded_box(xx, yy, 2 * s, 6.5 * s, 10 * s, 6.5 * s, 1.5 * s)
    cx, cy, ro, th = 7 * s, 6.5 * s, 3.8 * s, 1.8 * s
    d = np.sqrt((xx - cx) ** 2 + (np.minimum(yy, cy) - cy) ** 2)
    ring = np.clip(0.5 - (np.abs(d - (ro - th / 2)) - th / 2), 0, 1)
    ring[yy > 7 * s] = 0
    return np.maximum(body, ring).astype(np.float32)


def _rounded_box(xx, yy, x0, y0, w, h, r) -> np.ndarray:
    qx = np.abs(xx - (x0 + w / 2)) - (w / 2 - r)
    qy = np.abs(yy - (y0 + h / 2)) - (h / 2 - r)
    d = np.sqrt(np.maximum(qx, 0) ** 2 + np.maximum(qy, 0) ** 2) + np.minimum(np.maximum(qx, qy), 0) - r
    return np.clip(0.5 - d, 0.0, 1.0).astype(np.float32)


class PillRenderer:
    """Кадр таблетки: BGRA с предумноженной прозрачностью, как ждёт Windows.

    scale — масштаб экрана (1.0 = 100%, 1.5 = 150%).
    """

    BREATH_STEPS = 5

    def __init__(self, scale: float = 1.0):
        self.scale = s = float(scale)
        self.pad = round(GLOW_PAD * s)
        self.w = round(WIDTH * s)
        self.h = round(HEIGHT * s)
        self.width = self.w + 2 * self.pad
        self.height = self.h + 2 * self.pad
        yy, xx = np.mgrid[0:self.height, 0:self.width].astype(np.float32) + 0.5
        capsule = _stadium(xx, yy, self.pad, self.pad, self.w, self.h)
        self._capsule = capsule
        self._cap_a = capsule * CAPSULE_ALPHA
        self._inv_cap = 1.0 - self._cap_a
        self._cap_rgb = CAPSULE_GRAY * self._cap_a
        self._glow = {}               # размах -> маска свечения
        self._buf = np.zeros((self.height, self.width, 4), np.uint8)
        self._tmp = np.empty((self.height, self.width), np.float32)
        lock = round(14 * s)
        self._lock = _lock_mask(lock)
        self._lock_pos = (round(LOCK_X * s), (self.h - lock) // 2)
        self._bar_x = np.arange(self.w, dtype=np.float32) + 0.5
        self._bar_y = np.abs(np.arange(self.h, dtype=np.float32) + 0.5 - self.h / 2)
        pitch = (BAR_W + BAR_GAP) * s
        total = N_BARS * BAR_W * s + (N_BARS - 1) * BAR_GAP * s
        x0 = (self.w - total) / 2
        idx = np.clip(np.round((self._bar_x - x0 - BAR_W * s / 2) / pitch), 0, N_BARS - 1).astype(int)
        self._bar_idx = idx
        self._bar_dx = np.abs(self._bar_x - (x0 + idx * pitch + BAR_W * s / 2))

    def _glow_mask(self, spread: float) -> np.ndarray:
        """Маска свечения. Размах меняет только «Дыхание»: готовим несколько
        размытий заранее и смешиваем соседние."""
        steps = self.BREATH_STEPS
        pos = min(max(spread, 0.0), 1.0) * (steps - 1)
        lo = int(pos)
        hi = min(lo + 1, steps - 1)
        a, b = self._glow_at(lo), self._glow_at(hi)
        t = pos - lo
        return a if t < 1e-3 or lo == hi else a + (b - a) * t

    def _glow_at(self, step: int) -> np.ndarray:
        if step not in self._glow:
            # размах 0..1 ↔ радиус 14..30 на Mac; середина — обычное свечение
            sigma = GLOW_SIGMA * (7 + 8 * step / (self.BREATH_STEPS - 1)) / 11 * self.scale
            self._glow[step] = _blur(self._capsule, sigma)
        return self._glow[step]

    def _text_left(self, locked: bool) -> int:
        """Где может начинаться текст: при замке — правее значка."""
        side = round(TEXT_SIDE * self.scale)
        if not locked:
            return side
        return max(side, self._lock_pos[0] + self._lock.shape[1] + round(6 * self.scale))

    def timer_mask(self, text: str, text_mask):
        return text_mask(text, round(TIMER_PX * self.scale))

    def text_px(self, text: str, text_mask, locked: bool = False,
                reserved: int = 0) -> np.ndarray:
        limit = self.w - self._text_left(locked) - round(TEXT_SIDE * self.scale) - reserved
        px = TEXT_PX
        while True:
            mask = text_mask(text, round(px * self.scale))
            if mask.shape[1] <= limit or px <= TEXT_MIN_PX:
                return mask
            px -= 1

    def render(self, model: PillModel, now: float, text_mask) -> np.ndarray:
        """Кадр целиком. text_mask(text, px) -> float32 (h, w), 0..1."""
        op, spread, (dx, dy) = model.glow_params(now)
        r, g, b = model.color(now)
        glow = self._glow_mask(spread)
        if dx or dy:
            glow = _shift(glow, round(dx * self.scale), round(dy * self.scale))
        p = np.multiply(glow, self._inv_cap, out=self._tmp)
        p *= op
        alpha = self._cap_a + p
        rgb = [self._cap_rgb + p * c for c in (r, g, b)]

        box = (slice(self.pad, self.pad + self.h), slice(self.pad, self.pad + self.w))
        layers = []
        if model.locked:
            lx, ly = self._lock_pos
            lh, lw = self._lock.shape
            m = np.zeros((self.h, self.w), np.float32)
            m[ly:ly + lh, lx:lx + lw] = self._lock * 0.9
            layers.append((m, (1.0, 1.0, 1.0)))
        timer_w = 0
        if model.timer:
            tm = self.timer_mask(model.timer, text_mask)
            th, tw = tm.shape
            if th and tw:
                m = np.zeros((self.h, self.w), np.float32)
                x = self.w - round(TIMER_X * self.scale) - tw
                y = (self.h - th) // 2
                if x > 0 and y >= 0:
                    m[y:y + th, x:x + tw] = tm * TIMER_ALPHA
                    layers.append((m, (1.0, 1.0, 1.0)))
                    timer_w = tw + round(8 * self.scale)
        if model.mode == "wave":
            model.step_bars(now)
            layers.append((self._bars_mask(model.bars), BAR_COLOR))
        elif model.text:
            m = self._text_layer(model, now, text_mask, timer_w)
            if m is not None:
                layers.append((m, (1.0, 1.0, 1.0)))
        for m, col in layers:
            inv = 1.0 - m
            alpha[box] = m + alpha[box] * inv
            for i in range(3):
                rgb[i][box] = m * col[i] + rgb[i][box] * inv

        buf = self._buf
        for i, ch in enumerate((rgb[2], rgb[1], rgb[0], alpha)):   # BGRA
            np.clip(ch * 255 + 0.5, 0, 255, out=ch)
            buf[..., i] = ch
        return buf

    def _bars_mask(self, levels: np.ndarray) -> np.ndarray:
        s = self.scale
        heights = (BAR_MIN_H + levels * (BAR_MAX_H - BAR_MIN_H)) * s
        half_w = BAR_W * s / 2
        straight = np.maximum(heights[self._bar_idx] / 2 - half_w, 0)
        dy = np.maximum(self._bar_y[:, None] - straight[None, :], 0)
        d = np.sqrt(self._bar_dx[None, :] ** 2 + dy ** 2) - half_w
        return np.clip(0.5 - d, 0.0, 1.0).astype(np.float32)

    def _text_layer(self, model: PillModel, now: float, text_mask, reserved: int = 0):
        mask = self.text_px(model.text, text_mask, model.locked, reserved)
        th, tw = mask.shape
        if not th or not tw:
            return None
        t = (now - model.text_t0) / TEXT_APPEAR_SEC
        fade = _ease_out(t)
        rise = round(TEXT_RISE * self.scale * (1 - fade))
        layer = np.zeros((self.h, self.w), np.float32)
        x = (self.w - tw - reserved) // 2
        if model.locked:
            x = max(x, self._text_left(True))
        y = (self.h - th) // 2 + rise
        sx = max(0, -x)
        x = max(0, x)
        tw = min(tw - sx, self.w - x)
        th = min(th, self.h - y)
        if tw <= 0 or th <= 0:
            return None
        # Бегущий блик: полупрозрачный белый, по нему скользит яркая полоса
        # шириной 0.7 капсулы — от левого края за правый
        speed = SHIMMER_SEC.get(model.glow)
        cols = (np.arange(self.w, dtype=np.float32) + 0.5) / self.w
        if speed:
            phase = ((now - model.shimmer_t0) / speed) % 1.0
            center = -0.35 + 1.7 * phase
            bright = 0.62 + 0.38 * np.clip(1 - np.abs(cols - center) / 0.35, 0, 1)
        else:
            bright = np.full(self.w, 0.62, np.float32)
        layer[y:y + th, x:x + tw] = mask[:th, sx:sx + tw] * bright[x:x + tw] * fade
        return layer


# --- Вызовы из любых потоков ----------------------------------------------------

class PillController:
    """Интерфейс, который зовёт диктовка: show_text / show_wave / hide.

    Вызовы приходят из разных потоков, а рисовать можно только в потоке окна:
    post(fn) передаёт работу туда. surface — само окно: present(fresh)
    после изменения, withdraw() при скрытии; зовутся только в потоке окна.
    """

    def __init__(self, post, surface, model: PillModel | None = None,
                 clock=time.monotonic, timer=threading.Timer):
        self._post = post
        self._surface = surface
        self.model = model or PillModel()
        self._clock = clock
        self._timer = timer
        self._lock = threading.Lock()
        # «Поколение показа»: скрытие выполняется, только если после его
        # запроса таблетку не показали снова — иначе запоздалый hide
        # («Тишина», хвост прошлой диктовки) гасил бы уже новую запись
        self._gen = 0
        # Кадры волны склеиваются: в очереди всегда не больше одного, и он
        # рисует самый свежий уровень — волна показывает «сейчас», а не
        # догоняет пачкой, если поток окна отвлёкся
        self._wave_slot = None
        self._wave_queued = False

    def show_text(self, text: str, locked: bool = False, glow: str = "blue",
                  timer: str = "") -> None:
        with self._lock:
            self._gen += 1
            self._wave_slot = None

        def _show():
            fresh = self.model.show_text(text, locked, glow, self._clock(), timer)
            self._surface.present(fresh)
        self._post(_show)

    def show_wave(self, levels, locked: bool = False, glow: str = "blue",
                  timer: str = "") -> None:
        with self._lock:
            self._gen += 1
            self._wave_slot = (list(levels), locked, glow, timer)
            if self._wave_queued:
                return
            self._wave_queued = True
        self._post(self._draw_wave)

    def _draw_wave(self) -> None:
        with self._lock:
            self._wave_queued = False
            slot, self._wave_slot = self._wave_slot, None
        if slot is None:
            return
        levels, locked, glow, timer = slot
        fresh = self.model.show_wave(levels, locked, glow, self._clock(), timer)
        self._surface.present(fresh)

    def hide(self, after: float = 0.0) -> None:
        gen = self._gen

        def _hide():
            with self._lock:
                if self._gen != gen:
                    return
                self._wave_slot = None
            if self.model.visible:
                self.model.hide()
                self._surface.withdraw()

        if after > 0:
            t = self._timer(after, lambda: self._post(_hide))
            t.daemon = True
            t.start()
        else:
            self._post(_hide)

    def set_animation(self, name: str) -> None:
        def _apply():
            self.model.set_animation(name)
            if self.model.visible:
                self._surface.present(False)
        self._post(_apply)
