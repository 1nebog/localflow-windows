"""Значок в трее: рисуется кодом под размер и тему панели задач.

Картинок в программе нет: четыре столбика волны, цвет по состоянию.
Обычный — белый на тёмной панели и почти чёрный на светлой; запись —
красный; распознавание — зелёный, как свечение таблетки; модель ещё
грузится — бледный; ошибка — бледный с оранжевой точкой; пауза — бледный
и перечёркнутый.
"""

import numpy as np

STATES = ("idle", "loading", "recording", "busy", "error", "paused")

# высоты столбиков в долях значка
_BARS = (0.42, 0.9, 0.62, 0.3)

_RED = {False: (255, 69, 58), True: (215, 0, 21)}      # тёмная / светлая панель
_GREEN = {False: (48, 209, 88), True: (36, 138, 61)}
_ORANGE = (255, 159, 10)


def _fg(light: bool) -> tuple:
    return (28, 28, 30) if light else (255, 255, 255)


def _bars(size: int) -> np.ndarray:
    s = size / 16
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32) + 0.5
    bar_w, gap = 2.3 * s, 1.3 * s
    total = 4 * bar_w + 3 * gap
    x0 = (size - total) / 2
    out = np.zeros((size, size), np.float32)
    for i, h in enumerate(_BARS):
        cx = x0 + i * (bar_w + gap) + bar_w / 2
        hh = max(h * 14 * s, bar_w) / 2
        straight = hh - bar_w / 2
        dy = np.maximum(np.abs(yy - size / 2) - straight, 0)
        d = np.sqrt((xx - cx) ** 2 + dy ** 2) - bar_w / 2
        out = np.maximum(out, np.clip(0.5 - d, 0, 1))
    return out


def _dot(size: int, r: float, cx: float, cy: float) -> np.ndarray:
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32) + 0.5
    return np.clip(0.5 - (np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) - r), 0, 1)


def _slash(size: int, width: float) -> np.ndarray:
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32) + 0.5
    # отрезок из левого нижнего угла в правый верхний
    m = 1.5 * size / 16
    ax, ay, bx, by = m, size - m, size - m, m
    t = np.clip(((xx - ax) * (bx - ax) + (yy - ay) * (by - ay)) / ((bx - ax) ** 2 + (by - ay) ** 2), 0, 1)
    d = np.sqrt((xx - (ax + t * (bx - ax))) ** 2 + (yy - (ay + t * (by - ay))) ** 2) - width / 2
    return np.clip(0.5 - d, 0, 1)


def tray_icon(state: str, size: int = 16, light: bool = False) -> np.ndarray:
    """RGBA (size, size, 4), uint8, прозрачность не предумножена."""
    bars = _bars(size)
    color, alpha = _fg(light), bars
    if state == "recording":
        color = _RED[light]
    elif state == "busy":
        color = _GREEN[light]
    elif state in ("loading", "error", "paused"):
        alpha = bars * 0.45
    layers = [(alpha, color)]
    if state == "error":
        r = 3.2 * size / 16
        cx = cy = size - r - 0.3
        ring = _dot(size, r + 1.2 * size / 16, cx, cy)
        layers = [(alpha * (1 - ring), color), (_dot(size, r, cx, cy), _ORANGE)]
    elif state == "paused":
        s = size / 16
        cut = _slash(size, 4.2 * s)
        layers = [(alpha * (1 - cut), color), (_slash(size, 1.8 * s), _fg(light))]

    out = np.zeros((size, size, 4), np.float32)
    for a, col in layers:          # «поверх» без предумножения
        a = a.astype(np.float32)
        inv = 1 - a
        new_a = a + out[..., 3] * inv
        for i in range(3):
            num = col[i] * a + out[..., i] * out[..., 3] * inv
            out[..., i] = np.where(new_a > 0, num / np.maximum(new_a, 1e-6), 0)
        out[..., 3] = new_a
    out[..., 3] *= 255
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


_ACCENT = (10, 132, 255)


def app_icon(size: int = 64) -> np.ndarray:
    """Значок программы (окно панели, установщик): белая волна на синем
    скруглённом квадрате. RGBA, прозрачность не предумножена."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32) + 0.5
    r = size * 0.22
    half = size / 2 - size * 0.02
    qx = np.maximum(np.abs(xx - size / 2) - (half - r), 0)
    qy = np.maximum(np.abs(yy - size / 2) - (half - r), 0)
    square = np.clip(0.5 - (np.sqrt(qx ** 2 + qy ** 2) - r), 0, 1)
    inner = round(size * 0.62)
    bars = np.zeros((size, size), np.float32)
    o = (size - inner) // 2
    bars[o:o + inner, o:o + inner] = _bars(inner)
    out = np.zeros((size, size, 4), np.float32)
    for i in range(3):
        out[..., i] = _ACCENT[i] * (1 - bars) + 255 * bars
    out[..., 3] = square * 255
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def png_bytes(rgba: np.ndarray) -> bytes:
    """RGBA → PNG без сторонних библиотек."""
    import struct
    import zlib

    h, w, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
