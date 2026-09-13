"""Снимок окна панели на настоящей Windows (GitHub Actions).

    python tools/panel_snapshot.py build/panel.png

Запускает панель настоящей программы (без модели), открывает окно Edge,
ждёт, пока страница загрузится, и снимает окно в картинку.
"""

import ctypes
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]

from pill_snapshot import grab, write_png  # noqa: E402

from localflow import app as app_mod, core, panel  # noqa: E402
from localflow.win import panel_window, w32  # noqa: E402


def title(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    w32.user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "build/panel.png")
    w32.set_dpi_aware()
    a = app_mod.App()
    core._UI_LANG = "ru"
    srv = panel.PanelServer(a)
    assert srv.start()
    win = panel_window.PanelWindow(Path(tempfile.mkdtemp(prefix="lf-panel-")))
    t0 = time.monotonic()
    win.open(srv.url + "#settings")
    hwnd = None
    while time.monotonic() - t0 < 40:
        wins = win.windows()
        if wins and title(wins[0]) == "LocalFlow":
            hwnd = wins[0]
            break
        time.sleep(0.25)
    if hwnd is None:
        print("Окно панели не появилось:", [title(h) for h in win.windows()])
        sys.exit(1)
    print(f"Окно панели за {time.monotonic() - t0:.1f} c")
    time.sleep(2.5)                                   # страница дорисовывается
    w32.user32.SetWindowPos(hwnd, w32.HWND_TOPMOST, 0, 0, 0, 0,
                            w32.SWP_NOMOVE | w32.SWP_NOSIZE | w32.SWP_NOACTIVATE)
    time.sleep(0.5)
    rect = wintypes.RECT()
    w32.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    write_png(out, grab(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top))
    print(f"Снимок: {out}")
    win.close()
    srv.stop()


if __name__ == "__main__":
    main()
