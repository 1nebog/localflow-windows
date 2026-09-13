"""Все проверки пишут настройки, историю и журналы во временную папку,
а не в настоящие папки программы на этом компьютере."""

import os
import tempfile
import time

import pytest

os.environ.setdefault("LOCALFLOW_HOME", tempfile.mkdtemp(prefix="lf-test-"))


@pytest.fixture(scope="module")
def window():
    """Настоящее окно Windows с полем ввода, на переднем плане.
    (root, text, focused): focused=False — окно не получило фокус."""
    import ctypes
    import tkinter as tk

    user32 = ctypes.WinDLL("user32")
    root = tk.Tk()
    root.title("LocalFlow test window")
    root.geometry("500x200+80+80")
    text = tk.Text(root)
    text.pack(fill="both", expand=True)
    root.attributes("-topmost", True)
    root.update()
    hwnd = int(root.wm_frame(), 16)
    for _ in range(20):
        user32.SetForegroundWindow(hwnd)
        root.focus_force()
        text.focus_set()
        root.update()
        if user32.GetForegroundWindow() == hwnd:
            break
        time.sleep(0.1)
    focused = user32.GetForegroundWindow() == hwnd
    yield root, text, focused
    root.destroy()


def pump(root, sec=0.4):
    """Дать окну обработать пришедшие нажатия."""
    end = time.monotonic() + sec
    while time.monotonic() < end:
        root.update()
        time.sleep(0.01)
