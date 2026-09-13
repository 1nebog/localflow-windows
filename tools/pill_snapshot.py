"""Снимок таблетки на настоящей Windows и замер нагрузки на процессор.

    python tools/pill_snapshot.py build/pill.png

Показывает таблетку в разных состояниях, снимает экран вокруг неё и
складывает кадры в одну картинку. Заодно меряет, сколько процессора уходит
на анимацию записи и на спрятанную таблетку. Запускается в GitHub Actions.
"""

import ctypes
import struct
import sys
import threading
import time
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from localflow import core, strings  # noqa: E402
from localflow.win import overlay, ui, w32  # noqa: E402

SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
w32._proto(w32.gdi32.BitBlt, ctypes.c_int, w32.wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
           ctypes.c_int, w32.wintypes.HDC, ctypes.c_int, ctypes.c_int, w32.wintypes.DWORD)


def grab(x, y, w, h) -> np.ndarray:
    screen = w32.user32.GetDC(None)
    dc = w32.gdi32.CreateCompatibleDC(screen)
    bmp, bits = w32.dib(w, h)
    old = w32.gdi32.SelectObject(dc, bmp)
    w32.gdi32.BitBlt(dc, 0, 0, w, h, screen, x, y, SRCCOPY | CAPTUREBLT)
    raw = np.ctypeslib.as_array(ctypes.cast(bits, ctypes.POINTER(ctypes.c_ubyte)), shape=(h, w, 4))
    rgb = raw[..., [2, 1, 0]].copy()
    w32.gdi32.SelectObject(dc, old)
    w32.gdi32.DeleteObject(bmp)
    w32.gdi32.DeleteDC(dc)
    w32.user32.ReleaseDC(None, screen)
    return rgb


def write_png(path: Path, rgb: np.ndarray) -> None:
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def cpu_share(loop, sec) -> float:
    t0, c0 = time.perf_counter(), time.process_time()
    loop.pump(sec)
    return (time.process_time() - c0) / (time.perf_counter() - t0) * 100


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "build/pill.png")
    strings.install()
    core._UI_LANG = "ru"
    loop = ui.UiLoop()
    pill = overlay.PillWindow(loop)
    levels = np.abs(np.sin(np.arange(20) * 0.7)) * 0.9
    states = [
        lambda: pill.show_text(core.tr("speak")),
        lambda: pill.show_wave(levels),
        lambda: pill.show_wave(levels, locked=True, glow="red"),
        lambda: pill.show_text(core.tr("transcribing"), glow="green"),
        lambda: pill.show_text(core.tr("silence"), locked=True, glow="red"),
    ]
    shots = []
    for show in states:
        show()
        loop.pump(1.6)
        r = pill._renderer
        x, y = pill._origin
        shots.append(grab(x, y, r.width, r.height))
        pill.hide()
        loop.pump(0.2)
    write_png(out, np.concatenate(shots, axis=0))
    print(f"Снимок: {out} (масштаб экрана {pill._renderer.scale:.2f})")

    # Нагрузка: запись с волной 10 раз в секунду, как во время диктовки
    stop = threading.Event()

    def feed():
        rng = np.random.default_rng(0)
        while not stop.is_set():
            pill.show_wave(rng.random(20), glow="blue")
            time.sleep(0.1)

    loop.pump(0.3)
    th = threading.Thread(target=feed, daemon=True)
    th.start()
    loop.pump(0.5)
    frames0 = pill.frames
    busy = cpu_share(loop, 5.0)
    frames = pill.frames - frames0
    stop.set()
    th.join()
    pill.hide()
    loop.pump(0.3)
    idle = cpu_share(loop, 3.0)
    print(f"Процессор во время записи: {busy:.1f}% одного ядра, {frames / 5:.0f} кадров/с")
    print(f"Процессор со спрятанной таблеткой: {idle:.2f}%")
    pill.destroy()
    loop.destroy()


if __name__ == "__main__":
    main()
