"""Окно таблетки: без рамки, поверх всех окон, не забирает фокус, клики
проходят сквозь.

Картинку считает `pill.PillRenderer`, окно лишь показывает готовые пиксели
с прозрачностью (layered window). Кадры идут по таймеру, только пока есть
что анимировать; спрятанная таблетка не стоит ничего.
"""

import ctypes
import logging
import time
from collections import OrderedDict
from ctypes import wintypes

import numpy as np

from .. import pill
from . import w32

log = logging.getLogger("localflow")

EX_STYLE = (w32.WS_EX_LAYERED | w32.WS_EX_TRANSPARENT | w32.WS_EX_TOPMOST
            | w32.WS_EX_TOOLWINDOW | w32.WS_EX_NOACTIVATE)


class GdiText:
    """Маска текста системным шрифтом (Segoe UI), с кэшем."""

    FACE = "Segoe UI"

    def __init__(self, limit: int = 48):
        self._cache: OrderedDict = OrderedDict()
        self._limit = limit

    def mask(self, text: str, px: int) -> np.ndarray:
        key = (text, px)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        m = self._render(text, px)
        self._cache[key] = m
        if len(self._cache) > self._limit:
            self._cache.popitem(last=False)
        return m

    def _render(self, text: str, px: int) -> np.ndarray:
        gdi = w32.gdi32
        dc = gdi.CreateCompatibleDC(None)
        font = gdi.CreateFontW(-px, 0, 0, 0, w32.FW_SEMIBOLD, 0, 0, 0, w32.DEFAULT_CHARSET,
                               0, 0, w32.ANTIALIASED_QUALITY, 0, self.FACE)
        old_font = gdi.SelectObject(dc, font)
        try:
            size = wintypes.SIZE()
            gdi.GetTextExtentPoint32W(dc, text, len(text), ctypes.byref(size))
            w, h = size.cx + 2, size.cy
            if w <= 2 or h <= 0:
                return np.zeros((0, 0), np.float32)
            bmp, bits = w32.dib(w, h)
            ctypes.memset(bits, 0, w * h * 4)
            old_bmp = gdi.SelectObject(dc, bmp)
            try:
                gdi.SetBkMode(dc, w32.TRANSPARENT)
                gdi.SetTextColor(dc, 0xFFFFFF)
                gdi.TextOutW(dc, 1, 0, text, len(text))
                raw = np.ctypeslib.as_array(ctypes.cast(bits, ctypes.POINTER(ctypes.c_ubyte)),
                                            shape=(h, w, 4))
                # белый текст на чёрном: яркость пикселя и есть непрозрачность
                return raw[..., :3].max(axis=2).astype(np.float32) / 255
            finally:
                gdi.SelectObject(dc, old_bmp)
                gdi.DeleteObject(bmp)
        finally:
            gdi.SelectObject(dc, old_font)
            gdi.DeleteObject(font)
            gdi.DeleteDC(dc)


class PillWindow:
    """Таблетка для диктовки: show_text / show_wave / hide из любых потоков."""

    FRAME_SEC = 1 / 30

    def __init__(self, ui, animation: str | None = None, clock=time.monotonic):
        self.ui = ui
        self._clock = clock
        self.hwnd = w32.create_window("LocalFlowPill", self._on_message,
                                      ex_style=EX_STYLE, style=w32.WS_POPUP)
        self._text = GdiText()
        self._renderer: pill.PillRenderer | None = None
        self._dc = None
        self._bmp = None
        self._bits = None
        self._origin = (0, 0)
        self._timer = None
        self._shown = False
        self._topmost_t = -1e9
        self.frames = 0                 # для проверок: сколько кадров нарисовано
        self.controller = pill.PillController(ui.post, self, pill.PillModel(animation or ""),
                                              clock=clock)
        self.model = self.controller.model
        self.show_text = self.controller.show_text
        self.show_wave = self.controller.show_wave
        self.hide = self.controller.hide
        self.set_animation = self.controller.set_animation

    def prewarm(self) -> None:
        """Размытие свечения посчитать заранее, пока никто не ждёт таблетку."""
        def _warm():
            self._place()
            self._renderer.render(self.model, 0.0, self._text.mask)
        self.ui.post(_warm)

    # --- Поток окна ---

    def _on_message(self, msg, wparam, lparam):
        if msg == w32.WM_MOUSEACTIVATE:
            return w32.MA_NOACTIVATE
        if msg == w32.WM_NCHITTEST:
            return w32.HTTRANSPARENT
        return None

    def _place(self) -> None:
        """Внизу по центру экрана, где курсор; масштаб — этого экрана."""
        work, scale = w32.monitor_at_cursor()
        if self._renderer is None or abs(self._renderer.scale - scale) > 1e-3:
            self._renderer = pill.PillRenderer(scale)
            self._make_surface()
        r = self._renderer
        x = work.left + (work.right - work.left - r.width) // 2
        y = work.bottom - round(pill.BOTTOM_GAP * scale) - r.h - r.pad
        self._origin = (x, y)

    def _make_surface(self) -> None:
        self._free_surface()
        r = self._renderer
        self._dc = w32.gdi32.CreateCompatibleDC(None)
        self._bmp, self._bits = w32.dib(r.width, r.height)
        w32.gdi32.SelectObject(self._dc, self._bmp)

    def _free_surface(self) -> None:
        if self._dc:
            w32.gdi32.DeleteDC(self._dc)
            self._dc = None
        if self._bmp:
            w32.gdi32.DeleteObject(self._bmp)
            self._bmp = None

    def present(self, fresh: bool) -> None:
        now = self._clock()
        if fresh or self._renderer is None:
            self._place()
        self._draw(now)
        if fresh or not self._shown:
            w32.user32.ShowWindow(self.hwnd, w32.SW_SHOWNOACTIVATE)
            self._shown = True
        if fresh or now - self._topmost_t > 1.0:
            # поверх всех: другое «всегда сверху» окно могло перекрыть
            self._topmost_t = now
            w32.user32.SetWindowPos(self.hwnd, w32.HWND_TOPMOST, 0, 0, 0, 0,
                                    w32.SWP_NOMOVE | w32.SWP_NOSIZE | w32.SWP_NOACTIVATE)
        if self._timer is None and self.model.animating(now):
            self._timer = self.ui.every(self.FRAME_SEC, self._tick)

    def withdraw(self) -> None:
        self.ui.cancel(self._timer)
        self._timer = None
        if self._shown:
            w32.user32.ShowWindow(self.hwnd, w32.SW_HIDE)
            self._shown = False

    def _tick(self) -> None:
        now = self._clock()
        if not self.model.visible:
            self.withdraw()
            return
        self._draw(now)
        if not self.model.animating(now):
            self.ui.cancel(self._timer)
            self._timer = None

    def _draw(self, now: float) -> None:
        r = self._renderer
        buf = r.render(self.model, now, self._text.mask)
        ctypes.memmove(self._bits, buf.ctypes.data, buf.nbytes)
        alpha, drop = self.model.appear(now)
        pos = wintypes.POINT(self._origin[0], self._origin[1] + round(drop * r.scale))
        size = wintypes.SIZE(r.width, r.height)
        src = wintypes.POINT(0, 0)
        blend = w32.BLENDFUNCTION(w32.AC_SRC_OVER, 0, max(0, min(255, round(alpha * 255))),
                                  w32.AC_SRC_ALPHA)
        if not w32.user32.UpdateLayeredWindow(self.hwnd, None, ctypes.byref(pos), ctypes.byref(size),
                                              self._dc, ctypes.byref(src), 0,
                                              ctypes.byref(blend), w32.ULW_ALPHA):
            log.debug("Таблетка: кадр не принят (%s)", ctypes.get_last_error())
        self.frames += 1

    def destroy(self) -> None:
        self.withdraw()
        self._free_surface()
        w32.destroy_window(self.hwnd)
