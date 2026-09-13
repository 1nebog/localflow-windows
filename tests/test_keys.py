"""Клавиша диктовки: нажатия, сочетания, Enter и назначение новой клавиши."""

import threading

from localflow.keys import (
    DEFAULT_HOTKEY, ENTER, ESCAPE, HOTKEY_CHORD, HOTKEY_DOWN, HOTKEY_UP,
    VK_LCONTROL, VK_LMENU, VK_LSHIFT, VK_LWIN, VK_RCONTROL, VK_RETURN,
    VK_ESCAPE, VK_SPACE, KeyboardLogic, chord_from_config, chord_label,
    validate,
)

F9 = 0x78
C = 0x43


def press(kl, vk):
    return kl.feed(vk, True)


def release(kl, vk):
    return kl.feed(vk, False)


def test_default_is_right_ctrl():
    assert DEFAULT_HOTKEY == (VK_RCONTROL,)
    assert chord_label(DEFAULT_HOTKEY) == "RCtrl"


def test_hold_and_release_right_ctrl_passes_through():
    kl = KeyboardLogic()
    assert press(kl, VK_RCONTROL) == (False, [HOTKEY_DOWN])
    # автоповтор зажатой клавиши не порождает новых событий
    assert press(kl, VK_RCONTROL) == (False, [])
    assert release(kl, VK_RCONTROL) == (False, [HOTKEY_UP])


def test_left_ctrl_does_not_trigger():
    kl = KeyboardLogic()
    assert press(kl, VK_LCONTROL) == (False, [])
    assert release(kl, VK_LCONTROL) == (False, [])


def test_shortcut_with_hotkey_reports_chord():
    kl = KeyboardLogic()
    press(kl, VK_RCONTROL)
    assert press(kl, C) == (False, [HOTKEY_CHORD])   # RCtrl+C — копирование
    release(kl, C)
    assert release(kl, VK_RCONTROL)[1] == [HOTKEY_UP]


def test_hotkey_not_triggered_when_other_key_already_held():
    kl = KeyboardLogic()
    press(kl, VK_LSHIFT)
    assert press(kl, VK_RCONTROL) == (False, [])


def test_combo_hotkey_swallows_main_key_only():
    kl = KeyboardLogic((VK_LCONTROL, VK_SPACE))
    assert press(kl, VK_LCONTROL) == (False, [])
    assert press(kl, VK_SPACE) == (True, [HOTKEY_DOWN])
    assert press(kl, VK_SPACE) == (True, [])          # автоповтор тоже съеден
    assert release(kl, VK_SPACE) == (True, [HOTKEY_UP])
    assert release(kl, VK_LCONTROL) == (False, [])


def test_single_function_key_swallowed():
    kl = KeyboardLogic((F9,))
    assert press(kl, F9) == (True, [HOTKEY_DOWN])
    assert release(kl, F9) == (True, [HOTKEY_UP])


def test_enter_swallowed_only_when_armed_and_bare():
    kl = KeyboardLogic()
    assert press(kl, VK_RETURN) == (False, [])
    release(kl, VK_RETURN)
    kl.enter_armed = True
    assert press(kl, VK_RETURN) == (True, [ENTER])
    kl.enter_armed = False            # запись остановилась, пока Enter зажат
    assert release(kl, VK_RETURN) == (True, [])   # отпускание тоже съедаем
    kl.enter_armed = True
    press(kl, VK_LSHIFT)
    assert press(kl, VK_RETURN) == (False, [])    # Shift+Enter — приложению


def test_enter_while_holding_hotkey_is_not_chord():
    kl = KeyboardLogic()
    kl.enter_armed = True
    press(kl, VK_RCONTROL)
    assert press(kl, VK_RETURN) == (True, [ENTER])


def test_escape_passes_through_with_event():
    kl = KeyboardLogic()
    assert press(kl, VK_ESCAPE) == (False, [ESCAPE])


def test_lost_release_is_pruned():
    held = {VK_LSHIFT}
    kl = KeyboardLogic(is_down=lambda vk: vk in held)
    press(kl, VK_LSHIFT)
    held.clear()   # отпускание Shift потерялось (например, блокировка экрана)
    held.add(VK_RCONTROL)
    assert press(kl, VK_RCONTROL) == (False, [HOTKEY_DOWN])


def test_capture_new_hotkey():
    kl = KeyboardLogic()
    got = []
    done = threading.Event()
    kl.capture(lambda chord: (got.append(chord), done.set()))
    assert press(kl, VK_LCONTROL) == (True, [])
    assert press(kl, VK_SPACE) == (True, [])
    assert release(kl, VK_SPACE) == (True, [])
    assert not done.is_set()
    release(kl, VK_LCONTROL)
    assert done.wait(1)
    assert got == [(VK_LCONTROL, VK_SPACE)]
    kl.set_hotkey(got[0])
    press(kl, VK_LCONTROL)
    assert press(kl, VK_SPACE)[1] == [HOTKEY_DOWN]


def test_validation():
    assert validate([])[0] == "hk_err_empty"
    assert validate([VK_ESCAPE])[0] == "hk_err_reserved"
    assert validate([C])[0] == "hk_err_typing"
    assert validate([VK_SPACE])[0] == "hk_err_typing"
    assert validate([VK_LCONTROL, C]) == (None, [])
    assert validate([F9]) == (None, [])
    assert validate([VK_LMENU]) == (None, ["hk_warn_alt"])
    assert validate([VK_LWIN, VK_SPACE]) == (None, ["hk_warn_win"])


def test_config_roundtrip_and_garbage():
    assert chord_from_config([0x20, 0xA2]) == (VK_LCONTROL, VK_SPACE)
    assert chord_from_config("мусор") == DEFAULT_HOTKEY
    assert chord_from_config([0x43]) == DEFAULT_HOTKEY   # просто «C» нельзя
    assert chord_from_config(None) == DEFAULT_HOTKEY
