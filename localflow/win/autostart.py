"""Запуск вместе с Windows: строка в «автозагрузке» текущего пользователя.

Права администратора не нужны. Если человек выключил LocalFlow в Диспетчере
задач (вкладка «Автозагрузка»), Windows помечает это отдельно — такую
пометку тоже учитываем, иначе переключатель в панели врал бы.
"""

import logging
import sys
import winreg
from pathlib import Path

log = logging.getLogger("localflow")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
NAME = "LocalFlow"


def command() -> str:
    """Чем запускать: собранная программа — сама собой, из исходников —
    pythonw (без чёрного окна консоли)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    if not pyw.exists():
        pyw = exe
    root = Path(__file__).resolve().parents[2]
    return (f'"{pyw}" -c "import sys; sys.path.insert(0, r\'{root}\'); '
            f'from localflow.app import main; main()"')


def _read(key_path: str, name: str):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def is_enabled() -> bool:
    if not _read(RUN_KEY, NAME):
        return False
    approved = _read(APPROVED_KEY, NAME)
    # первый байт: 2 — включено, 3 — выключено в Диспетчере задач
    return not (isinstance(approved, bytes) and approved[:1] == b"\x03")


def set_enabled(on: bool) -> bool:
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if on:
                winreg.SetValueEx(key, NAME, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(key, NAME)
                except FileNotFoundError:
                    pass
        # снять пометку «выключено в Диспетчере задач»
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPROVED_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, NAME)
        except OSError:
            pass
    except OSError as exc:
        log.error("Автозапуск не переключился: %s", exc)
        return False
    log.info("Автозапуск %s", "включён" if on else "выключен")
    return is_enabled() == on
