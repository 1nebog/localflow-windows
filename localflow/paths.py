"""Где программа хранит свои файлы.

Настройки, словарь, сниппеты и история — в %APPDATA%\\LocalFlow: это личное
и маленькое. Модели и движки — в %LOCALAPPDATA%\\LocalFlow: они большие, и
в Roaming им не место (в рабочих сетях Roaming копируется между
компьютерами при каждом входе).

Переменная LOCALFLOW_HOME переносит всё в одну папку — для тестов и для
«портативного» запуска с флешки.
"""

import os
import sys
from pathlib import Path

APP_NAME = "LocalFlow"


def _roaming() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def _local() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


_home = os.environ.get("LOCALFLOW_HOME")

CONFIG_DIR = Path(_home) if _home else _roaming() / APP_NAME
DATA_DIR = Path(_home) if _home else _local() / APP_NAME
MODELS_DIR = DATA_DIR / "models"
ENGINES_DIR = DATA_DIR / "engines"
LOG_DIR = DATA_DIR / "logs"
