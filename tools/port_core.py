"""Переносит текстовую логику из маковой версии LocalFlow в localflow/core.py.

Зачем инструмент, а не копипаст руками: вся работа с текстом (чистка,
команды, фильтры ошибок Whisper, проверки умного исправления) у Mac и
Windows общая. Когда что-то чинится на Mac, достаточно запустить

    python tools/port_core.py путь/к/local_flow.py

и Windows получит ту же правку. Код переносится ДОСЛОВНО, меняются только
места из PATCHES — и каждая замена обязана сработать ровно столько раз,
сколько указано. Если на Mac текст поменялся и замена промахнулась,
инструмент падает, а не молча пропускает.

Что берём: все верхнеуровневые определения, которые не трогают маковые
библиотеки ни сами, ни через свои зависимости. Что не берём — EXCLUDE.
"""

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "localflow" / "core.py"

# По этим словам определение считается маковым
MAC_MARKERS = (
    "AppKit", "Quartz", "rumps", "AppHelper", "objc", "Foundation", "mlx",
    "ctypes", "CoreAudio", "MediaRemote", "NSWorkspace", "AXUIElement",
    "osascript", "afconvert", "launchctl", "pynput", "keyboard.",
    "sounddevice", "sd.", "pyperclip", "subprocess", "CGEvent", "NSPanel",
    "CALayer",
)

# Чистые по коду, но на Windows не нужны или заменяются своими
EXCLUDE = {
    # маковые пути, звуки и журнал — у Windows свои
    "CONFIG_DIR", "HISTORY_HTML_PATH", "SOUND_START", "SOUND_STOP",
    "SOUND_CANCEL", "_log_fmt", "_file_handler", "_stream_handler", "log",
    # модели MLX и проверка их скачивания — движки на Windows другие
    "MLX_REPOS", "LLM_REPOS", "model_is_downloaded",
    # пауза музыки через MediaRemote
    "_MR_PLAY", "_MR_PAUSE", "_music_lock", "_media_paused",
    "_media_paused_pid",
    # голосовой вызов «Hey Flow» выключен насовсем
    "WAKEWORD_DIR", "WAKE_THRESHOLD", "WAKE_CONSECUTIVE", "WAKE_STRONG",
    "WAKE_DEBOUNCE_SEC", "WAKE_RETRAIN_NEW", "WAKE_TRAIN_DIR",
    "WAKE_TRAIN_STATE",
    # иконки menu bar и ссылка на приложение
    "ICON_IDLE", "ICON_RECORDING", "ICON_PROCESSING", "ICON_LOADING", "_APP",
    # панель переносится отдельным модулем
    "PANEL_STRINGS", "PANEL_HTML",
    # версия, ссылка и «Что нового» у Mac свои (у Windows — news.py)
    "APP_VERSION", "APP_PAGE_URL", "MAC_NEWS",
}

# (шаблон, замена, сколько раз обязано встретиться). Шаблоны нарочно общие:
# личные строки не должны лежать даже здесь, файл публичный.
EMAIL = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
PATCHES = [
    # Личные данные из примера сниппетов — в публичный код не идут
    (r"(#\s+моя подпись = С уважением,\\\\n)[^\n]+", r"\1Имя Фамилия", 1),
    (rf"\n(моя почта = ){EMAIL}\n", r"\n# \1name@example.com\n", 1),
    # Сочетания клавиш
    (r"\(Cmd\+S\)", "(Ctrl+S)", 2),
    (r"Cmd\+V", "Ctrl+V", 4),
]

HEADER = '''"""Текстовая логика LocalFlow — общая с версией для macOS.

ФАЙЛ СОБРАН АВТОМАТИЧЕСКИ инструментом tools/port_core.py из маковой версии.
Руками не править: правка потеряется при следующем переносе. Всё, что
касается только Windows, живёт в соседних модулях.
"""

import difflib
import json
import logging
import re
import threading
import time
from pathlib import Path

import numpy as np

from .paths import CONFIG_DIR

log = logging.getLogger("localflow")

'''


def top_level(tree):
    """[(имена, узел)] в порядке исходника."""
    out = []
    for node in tree.body:
        names = []
        if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.append(t.id)
                elif isinstance(t, ast.Tuple):
                    names += [e.id for e in t.elts if isinstance(e, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        if names:
            out.append((names, node))
    return out


def main(src_path: str) -> None:
    src = Path(src_path).read_text(encoding="utf-8")
    lines = src.splitlines()
    tree = ast.parse(src)
    items = top_level(tree)
    by_name = {n: node for names, node in items for n in names}

    def body(node):
        return "\n".join(lines[node.lineno - 1:node.end_lineno])

    deps, mac = {}, {}
    for names, node in items:
        used = {x.id for x in ast.walk(node) if isinstance(x, ast.Name)}
        for n in names:
            deps[n] = {u for u in used if u in by_name and u != n}
            mac[n] = any(m in body(node) for m in MAC_MARKERS)

    memo = {}

    def tainted(n, stack=()):
        if n in memo:
            return memo[n]
        if mac[n]:
            memo[n] = True
            return True
        res = any(tainted(d, stack + (n,)) for d in deps[n] if d not in stack)
        memo[n] = res
        return res

    keep_nodes, kept_names = [], set()
    prev_end = 0
    for names, node in items:
        start = node.lineno
        # Захватываем комментарии прямо над определением
        while start - 1 > prev_end and lines[start - 2].lstrip().startswith("#"):
            start -= 1
        wanted = [n for n in names if n not in EXCLUDE and not tainted(n)]
        if wanted:
            keep_nodes.append("\n".join(lines[start - 1:node.end_lineno]))
            kept_names.update(names)
        prev_end = node.end_lineno

    # Всё, от чего зависят взятые определения, тоже должно быть взято
    provided = {"CONFIG_DIR", "log"}
    missing = sorted({d for n in kept_names for d in deps[n]}
                     - kept_names - provided)
    if missing:
        sys.exit(f"Взятые определения ссылаются на невзятые: {missing}")

    text = HEADER + "\n\n".join(keep_nodes) + "\n"
    for pattern, new, count in PATCHES:
        text, found = re.subn(pattern, new, text)
        if found != count:
            sys.exit(f"Замена {pattern!r}: ожидалось {count}, найдено {found}")

    # Страховка: никаких адресов почты, кроме примера, и маковых путей
    emails = [e for e in re.findall(EMAIL, text) if not e.endswith("example.com")]
    if emails:
        sys.exit(f"В перенесённом коде остались адреса почты: {len(emails)} шт.")
    for leak in ("/Library/", "/tmp/"):
        if leak in text:
            sys.exit(f"В перенесённом коде осталось {leak!r}")

    OUT.write_text(text, encoding="utf-8")
    print(f"Готово: {OUT.relative_to(ROOT)} — {len(kept_names)} определений, "
          f"{text.count(chr(10))} строк")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Использование: python tools/port_core.py путь/к/local_flow.py")
    main(sys.argv[1])
