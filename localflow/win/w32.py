"""Функции Windows для окон, трея и рисования — через ctypes, без библиотек.

Типы аргументов прописаны явно: без них на 64-битной Windows ctypes режет
указатели и дескрипторы до 32 бит, и программа падает в случайном месте.
"""

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
try:
    shcore = ctypes.WinDLL("shcore", use_last_error=True)
except OSError:          # Windows 7/8.0 — масштаб берём у системы
    shcore = None

LRESULT = ctypes.c_ssize_t
UINT_PTR = ctypes.c_size_t
HANDLE = wintypes.HANDLE

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
WINEVENTPROC = ctypes.WINFUNCTYPE(None, HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG,
                                  wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
HANDLER_ROUTINE = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

# --- Сообщения и флаги ----------------------------------------------------------

WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUERYENDSESSION = 0x0011
WM_QUIT = 0x0012
WM_ENDSESSION = 0x0016
WM_SETTINGCHANGE = 0x001A
WM_MOUSEACTIVATE = 0x0021
WM_NCHITTEST = 0x0084
WM_CONTEXTMENU = 0x007B
WM_TIMER = 0x0113
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_DPICHANGED = 0x02E0
WM_APP = 0x8000

MA_NOACTIVATE = 3
HTTRANSPARENT = -1

WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

ULW_ALPHA = 0x02
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0
DIB_RGB_COLORS = 0

MONITOR_DEFAULTTONEAREST = 2
MDT_EFFECTIVE_DPI = 0

FW_SEMIBOLD = 600
DEFAULT_CHARSET = 1
ANTIALIASED_QUALITY = 4
TRANSPARENT = 1

SM_CXSMICON = 49

MF_STRING = 0x0000
MF_GRAYED = 0x0001
MF_CHECKED = 0x0008
MF_POPUP = 0x0010
MF_SEPARATOR = 0x0800
TPM_RIGHTBUTTON = 0x0002
TPM_BOTTOMALIGN = 0x0020
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100

NIM_ADD, NIM_MODIFY, NIM_DELETE, NIM_SETVERSION = 0, 1, 2, 4
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO, NIF_SHOWTIP = 0x01, 0x02, 0x04, 0x10, 0x80
NIIF_INFO, NIIF_WARNING, NIIF_ERROR = 1, 2, 3
NIIF_RESPECT_QUIET_TIME = 0x80
NOTIFYICON_VERSION_4 = 4
NIN_SELECT = 0x0400
NIN_KEYSELECT = 0x0401

EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002

# --- Структуры --------------------------------------------------------------------


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", HANDLE),
                ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HBITMAP),
                ("hbmColor", wintypes.HBITMAP)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD), ("guidItem", GUID),
                ("hBalloonIcon", wintypes.HICON)]


def _proto(fn, restype, *argtypes):
    fn.restype = restype
    fn.argtypes = argtypes


_proto(user32.RegisterClassExW, wintypes.ATOM, ctypes.POINTER(WNDCLASSEXW))
_proto(user32.CreateWindowExW, wintypes.HWND, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
       wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
       wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
_proto(user32.DefWindowProcW, LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_proto(user32.DestroyWindow, wintypes.BOOL, wintypes.HWND)
_proto(user32.ShowWindow, wintypes.BOOL, wintypes.HWND, ctypes.c_int)
_proto(user32.IsWindowVisible, wintypes.BOOL, wintypes.HWND)
_proto(user32.IsWindow, wintypes.BOOL, wintypes.HWND)
_proto(user32.SetWindowPos, wintypes.BOOL, wintypes.HWND, wintypes.HWND, ctypes.c_int,
       ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
_proto(user32.UpdateLayeredWindow, wintypes.BOOL, wintypes.HWND, wintypes.HDC,
       ctypes.POINTER(wintypes.POINT), ctypes.POINTER(wintypes.SIZE), wintypes.HDC,
       ctypes.POINTER(wintypes.POINT), wintypes.COLORREF, ctypes.POINTER(BLENDFUNCTION),
       wintypes.DWORD)
_proto(user32.GetDC, wintypes.HDC, wintypes.HWND)
_proto(user32.ReleaseDC, ctypes.c_int, wintypes.HWND, wintypes.HDC)
_proto(user32.GetMessageW, wintypes.BOOL, ctypes.POINTER(wintypes.MSG), wintypes.HWND,
       wintypes.UINT, wintypes.UINT)
_proto(user32.PeekMessageW, wintypes.BOOL, ctypes.POINTER(wintypes.MSG), wintypes.HWND,
       wintypes.UINT, wintypes.UINT, wintypes.UINT)
_proto(user32.TranslateMessage, wintypes.BOOL, ctypes.POINTER(wintypes.MSG))
_proto(user32.DispatchMessageW, LRESULT, ctypes.POINTER(wintypes.MSG))
_proto(user32.PostMessageW, wintypes.BOOL, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_proto(user32.PostQuitMessage, None, ctypes.c_int)
_proto(user32.SetTimer, UINT_PTR, wintypes.HWND, UINT_PTR, wintypes.UINT, wintypes.LPVOID)
_proto(user32.KillTimer, wintypes.BOOL, wintypes.HWND, UINT_PTR)
_proto(user32.GetCursorPos, wintypes.BOOL, ctypes.POINTER(wintypes.POINT))
_proto(user32.MonitorFromPoint, wintypes.HMONITOR, wintypes.POINT, wintypes.DWORD)
_proto(user32.GetMonitorInfoW, wintypes.BOOL, wintypes.HMONITOR, ctypes.POINTER(MONITORINFO))
_proto(user32.RegisterWindowMessageW, wintypes.UINT, wintypes.LPCWSTR)
_proto(user32.CreatePopupMenu, wintypes.HMENU)
_proto(user32.AppendMenuW, wintypes.BOOL, wintypes.HMENU, wintypes.UINT, UINT_PTR, wintypes.LPCWSTR)
_proto(user32.TrackPopupMenuEx, ctypes.c_int, wintypes.HMENU, wintypes.UINT, ctypes.c_int,
       ctypes.c_int, wintypes.HWND, wintypes.LPVOID)
_proto(user32.DestroyMenu, wintypes.BOOL, wintypes.HMENU)
_proto(user32.SetForegroundWindow, wintypes.BOOL, wintypes.HWND)
_proto(user32.GetForegroundWindow, wintypes.HWND)
_proto(user32.GetClassNameW, ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_proto(user32.GetWindowThreadProcessId, wintypes.DWORD, wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
_proto(user32.CreateIconIndirect, wintypes.HICON, ctypes.POINTER(ICONINFO))
_proto(user32.DestroyIcon, wintypes.BOOL, wintypes.HICON)
_proto(user32.GetSystemMetrics, ctypes.c_int, ctypes.c_int)
_proto(user32.SetWinEventHook, HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE,
       WINEVENTPROC, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD)
_proto(user32.UnhookWinEvent, wintypes.BOOL, HANDLE)

_proto(gdi32.CreateCompatibleDC, wintypes.HDC, wintypes.HDC)
_proto(gdi32.DeleteDC, wintypes.BOOL, wintypes.HDC)
_proto(gdi32.CreateDIBSection, wintypes.HBITMAP, wintypes.HDC, ctypes.POINTER(BITMAPINFO),
       wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), HANDLE, wintypes.DWORD)
_proto(gdi32.CreateBitmap, wintypes.HBITMAP, ctypes.c_int, ctypes.c_int, wintypes.UINT,
       wintypes.UINT, wintypes.LPVOID)
_proto(gdi32.SelectObject, wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
_proto(gdi32.DeleteObject, wintypes.BOOL, wintypes.HGDIOBJ)
_proto(gdi32.CreateFontW, wintypes.HFONT, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
       ctypes.c_int, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
       wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR)
_proto(gdi32.SetTextColor, wintypes.COLORREF, wintypes.HDC, wintypes.COLORREF)
_proto(gdi32.SetBkMode, ctypes.c_int, wintypes.HDC, ctypes.c_int)
_proto(gdi32.GetTextExtentPoint32W, wintypes.BOOL, wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
       ctypes.POINTER(wintypes.SIZE))
_proto(gdi32.TextOutW, wintypes.BOOL, wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.LPCWSTR,
       ctypes.c_int)

_proto(shell32.Shell_NotifyIconW, wintypes.BOOL, wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW))
_proto(kernel32.GetModuleHandleW, wintypes.HMODULE, wintypes.LPCWSTR)
_proto(kernel32.GetCurrentProcessId, wintypes.DWORD)
_proto(kernel32.SetConsoleCtrlHandler, wintypes.BOOL, HANDLER_ROUTINE, wintypes.BOOL)
if shcore is not None:
    _proto(shcore.GetDpiForMonitor, ctypes.c_long, wintypes.HMONITOR, ctypes.c_int,
           ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT))


# --- Окна -------------------------------------------------------------------------

_handlers: dict[int, object] = {}
_classes: set[str] = set()


def _dispatch(hwnd, msg, wparam, lparam):
    handler = _handlers.get(hwnd)
    if handler is not None:
        try:
            res = handler(msg, wparam, lparam)
            if res is not None:
                return res
        except Exception:
            import logging
            logging.getLogger("localflow").exception("Окно: сообщение 0x%04X упало", msg)
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


_wndproc = WNDPROC(_dispatch)   # одна на все окна; ссылку держим до выхода


def create_window(class_name: str, handler, ex_style: int = 0, style: int = 0) -> int:
    """Окно в текущем потоке. handler(msg, wparam, lparam) -> результат или None."""
    hinst = kernel32.GetModuleHandleW(None)
    if class_name not in _classes:
        wc = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=_wndproc,
                         hInstance=hinst, lpszClassName=class_name)
        if not user32.RegisterClassExW(ctypes.byref(wc)) and ctypes.get_last_error() != 1410:
            raise ctypes.WinError(ctypes.get_last_error())
        _classes.add(class_name)
    hwnd = user32.CreateWindowExW(ex_style, class_name, "LocalFlow", style,
                                  0, 0, 0, 0, None, None, hinst, None)
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())
    _handlers[hwnd] = handler
    return hwnd


def destroy_window(hwnd: int) -> None:
    _handlers.pop(hwnd, None)
    user32.DestroyWindow(hwnd)


def monitor_at_cursor() -> tuple[wintypes.RECT, float]:
    """Рабочая область экрана под курсором (без панели задач) и его масштаб."""
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    mon = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(mon, ctypes.byref(info))
    scale = 1.0
    if shcore is not None:
        dx, dy = wintypes.UINT(), wintypes.UINT()
        if shcore.GetDpiForMonitor(mon, MDT_EFFECTIVE_DPI, ctypes.byref(dx), ctypes.byref(dy)) == 0:
            scale = dx.value / 96
    return info.rcWork, scale


def dib(width: int, height: int):
    """32-битная картинка сверху вниз: (дескриптор, адрес пикселей)."""
    bmi = BITMAPINFO()
    h = bmi.bmiHeader
    h.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    h.biWidth, h.biHeight = width, -height
    h.biPlanes, h.biBitCount, h.biCompression = 1, 32, BI_RGB
    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(None, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
    if not hbmp:
        raise ctypes.WinError(ctypes.get_last_error())
    return hbmp, bits.value


def set_dpi_aware() -> None:
    """Чёткая таблетка на экранах 125–200%: иначе Windows растягивает картинку."""
    try:
        user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        user32.SetProcessDpiAwarenessContext.argtypes = (HANDLE,)
        if user32.SetProcessDpiAwarenessContext(HANDLE(-4)):   # per-monitor v2
            return
    except AttributeError:
        pass
    try:
        user32.SetProcessDPIAware()
    except AttributeError:
        pass
