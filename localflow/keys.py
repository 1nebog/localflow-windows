"""Клавиша диктовки: какие клавиши зажаты, когда запись начать и остановить.

Здесь только логика — без Windows, чтобы её можно было проверять где угодно.
Перехват клавиатуры в самой Windows — в `win/keyhook.py`: он передаёт сюда
каждое нажатие и спрашивает, пропустить ли клавишу в приложение.

Клавиша диктовки — одна клавиша или сочетание, записанное кодами клавиш
Windows (virtual-key). По умолчанию правый Ctrl: сам по себе он ничего не
делает почти нигде, а под большой палец попадает на любой клавиатуре.
"""

import threading

# --- Коды клавиш Windows ----------------------------------------------------

VK_BACK, VK_TAB, VK_RETURN, VK_ESCAPE, VK_SPACE = 0x08, 0x09, 0x0D, 0x1B, 0x20
VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_LWIN, VK_RWIN = 0x5B, 0x5C
VK_LSHIFT, VK_RSHIFT = 0xA0, 0xA1
VK_LCONTROL, VK_RCONTROL = 0xA2, 0xA3
VK_LMENU, VK_RMENU = 0xA4, 0xA5

DEFAULT_HOTKEY = (VK_RCONTROL,)

MODIFIERS = {
    VK_SHIFT, VK_CONTROL, VK_MENU, VK_LSHIFT, VK_RSHIFT, VK_LCONTROL,
    VK_RCONTROL, VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN,
}
_CTRL = {VK_CONTROL, VK_LCONTROL, VK_RCONTROL}
_SHIFT = {VK_SHIFT, VK_LSHIFT, VK_RSHIFT}
_ALT = {VK_MENU, VK_LMENU, VK_RMENU}
_WIN = {VK_LWIN, VK_RWIN}

VK_NAMES = {
    VK_BACK: "Backspace", VK_TAB: "Tab", VK_RETURN: "Enter", 0x13: "Pause",
    0x14: "CapsLock", VK_ESCAPE: "Esc", VK_SPACE: "Space", 0x21: "PageUp",
    0x22: "PageDown", 0x23: "End", 0x24: "Home", 0x25: "←", 0x26: "↑",
    0x27: "→", 0x28: "↓", 0x2C: "PrintScreen", 0x2D: "Insert", 0x2E: "Delete",
    VK_LWIN: "LWin", VK_RWIN: "RWin", 0x5D: "Menu",
    0x6A: "Num*", 0x6B: "Num+", 0x6D: "Num-", 0x6E: "Num.", 0x6F: "Num/",
    0x90: "NumLock", 0x91: "ScrollLock",
    VK_SHIFT: "Shift", VK_CONTROL: "Ctrl", VK_MENU: "Alt",
    VK_LSHIFT: "LShift", VK_RSHIFT: "RShift", VK_LCONTROL: "LCtrl",
    VK_RCONTROL: "RCtrl", VK_LMENU: "LAlt", VK_RMENU: "RAlt",
    0xAD: "Mute", 0xAE: "Volume-", 0xAF: "Volume+", 0xB0: "NextTrack",
    0xB1: "PrevTrack", 0xB2: "Stop", 0xB3: "PlayPause",
}
VK_NAMES.update({0x30 + i: str(i) for i in range(10)})
VK_NAMES.update({0x41 + i: chr(ord("A") + i) for i in range(26)})
VK_NAMES.update({0x60 + i: f"Num{i}" for i in range(10)})
VK_NAMES.update({0x70 + i: f"F{i + 1}" for i in range(24)})


def key_name(vk: int) -> str:
    return VK_NAMES.get(vk, f"Key{vk:02X}")


def normalize(chord) -> tuple[int, ...]:
    """Модификаторы вперёд, дальше остальное; без повторов."""
    uniq = list(dict.fromkeys(int(v) for v in chord))
    return tuple(sorted(uniq, key=lambda v: (v not in MODIFIERS, v)))


def chord_label(chord) -> str:
    return "+".join(key_name(v) for v in normalize(chord))


def chord_from_config(value) -> tuple[int, ...]:
    """Из настроек: список кодов. Всё непонятное — клавиша по умолчанию."""
    try:
        chord = normalize(value)
    except (TypeError, ValueError):
        return DEFAULT_HOTKEY
    if not chord or validate(chord)[0]:
        return DEFAULT_HOTKEY
    return chord


def validate(chord) -> tuple[str | None, list[str]]:
    """(ошибка, предупреждения). Ошибка — ключ строки интерфейса, такую
    клавишу назначить нельзя; предупреждения — можно, но стоит знать."""
    chord = normalize(chord)
    if not chord:
        return "hk_err_empty", []
    if len(chord) > 3:
        return "hk_err_too_many", []
    keys = set(chord)
    if keys & {VK_ESCAPE, VK_RETURN}:
        # у них уже есть роль: Esc — отмена, Enter — «готово»
        return "hk_err_reserved", []
    others = [v for v in chord if v not in MODIFIERS]
    mods = keys & MODIFIERS
    # Буква, цифра, пробел без модификатора — сломает обычный набор текста
    if not mods and any(0x30 <= v <= 0x5A or v in (VK_SPACE, VK_TAB, VK_BACK)
                        for v in others):
        return "hk_err_typing", []
    warnings = []
    if not others and keys <= _ALT:
        warnings.append("hk_warn_alt")      # Alt в приложениях открывает меню
    if keys & _WIN:
        warnings.append("hk_warn_win")      # Win открывает «Пуск»
    if not others and keys <= _SHIFT:
        warnings.append("hk_warn_shift")    # Shift нужен для заглавных букв
    return None, warnings


# --- Логика нажатий ----------------------------------------------------------

HOTKEY_DOWN = "hotkey_down"
HOTKEY_UP = "hotkey_up"
HOTKEY_CHORD = "hotkey_chord"      # при зажатой клавише диктовки нажали другую
ENTER = "enter"
ESCAPE = "escape"


class KeyboardLogic:
    """Состояние клавиатуры и решения «пропустить или съесть».

    feed() зовётся из перехватчика на каждое нажатие и обязан отвечать
    мгновенно: Windows снимает перехватчик, который задумывается. Поэтому
    здесь никакой работы — только решение и список событий для программы.
    """

    def __init__(self, hotkey=DEFAULT_HOTKEY, is_down=None):
        self.hotkey = normalize(hotkey)
        # Enter съедается и превращается в «готово», пока идёт запись
        self.enter_armed = False
        # Проверка «клавиша правда зажата сейчас»: отпускание могло
        # потеряться (блокировка экрана, окно администратора)
        self._is_down = is_down
        self._down: set[int] = set()
        self._active = False
        self._swallowed: set[int] = set()   # съели нажатие — съедим и отпускание
        self._capture = None
        self._captured: list[int] = []
        self._lock = threading.Lock()

    @property
    def hotkey_held(self) -> bool:
        return self._active

    @property
    def capturing(self) -> bool:
        return self._capture is not None

    def set_hotkey(self, chord) -> None:
        with self._lock:
            self.hotkey = normalize(chord)
            self._active = False

    def capture(self, on_done) -> None:
        """Режим назначения клавиши: следующее нажатие (одна клавиша или
        сочетание) не уходит в приложения, а возвращается в on_done."""
        with self._lock:
            self._capture = on_done
            self._captured = []

    def cancel_capture(self) -> None:
        with self._lock:
            self._capture = None

    def _prune_stale(self) -> None:
        if self._is_down is None:
            return
        for vk in list(self._down):
            if vk in self._swallowed:
                continue   # съеденную клавишу Windows и не считает зажатой
            try:
                if not self._is_down(vk):
                    self._down.discard(vk)
            except Exception:
                pass

    def feed(self, vk: int, down: bool) -> tuple[bool, list[str]]:
        """Нажатие или отпускание. Возвращает (съесть ли клавишу, события)."""
        with self._lock:
            if self._capture is not None:
                return self._feed_capture(vk, down)
            events: list[str] = []
            if down:
                repeat = vk in self._down
                if not repeat:
                    self._prune_stale()
                    self._down.add(vk)
                chord = set(self.hotkey)
                if self._active:
                    if vk in chord:
                        # автоповтор зажатой клавиши диктовки
                        return vk in self._swallowed, events
                    if not repeat and vk not in (VK_RETURN, VK_ESCAPE):
                        events.append(HOTKEY_CHORD)
                elif not repeat and vk in chord and self._down == chord:
                    self._active = True
                    events.append(HOTKEY_DOWN)
                    # Обычную клавишу сочетания съедаем (иначе Ctrl+Space
                    # уйдёт ещё и в приложение), модификаторы — никогда:
                    # приложение осталось бы с «залипшим» Ctrl
                    if vk not in MODIFIERS:
                        self._swallowed.add(vk)
                        return True, events
                    return False, events
                if vk in (VK_RETURN, VK_ESCAPE) and not repeat:
                    others = self._down - {vk}
                    if vk == VK_ESCAPE:
                        events.append(ESCAPE)
                    elif self.enter_armed and not (others - chord):
                        # только «голый» Enter: Shift+Enter и Ctrl+Enter
                        # остаются приложению
                        events.append(ENTER)
                        self._swallowed.add(vk)
                        return True, events
                return vk in self._swallowed, events
            # отпускание
            self._down.discard(vk)
            swallow = vk in self._swallowed
            self._swallowed.discard(vk)
            if self._active and vk in self.hotkey:
                self._active = False
                events.append(HOTKEY_UP)
            return swallow, events

    def _feed_capture(self, vk: int, down: bool) -> tuple[bool, list[str]]:
        if down:
            self._down.add(vk)
            if vk not in self._captured:
                self._captured.append(vk)
            return True, []
        self._down.discard(vk)
        self._prune_stale()
        if not self._down and self._captured:
            done, chord = self._capture, normalize(self._captured)
            self._capture, self._captured = None, []
            threading.Thread(target=done, args=(chord,), daemon=True,
                             name="hotkey-captured").start()
        return True, []
