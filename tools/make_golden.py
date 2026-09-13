"""Снимает эталонные ответы маковой версии для tests/golden/core.json.

Запускать на Mac, интерпретатором маковой версии (там стоят её библиотеки):

    ../LocalFlow/.venv/bin/python tools/make_golden.py ../LocalFlow/local_flow.py

Тест tests/test_core_golden.py потом прогоняет те же входы через
localflow.core на Windows и требует совпадения символ в символ.
"""

import importlib.util
import json
import logging
import logging.handlers
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from corpus import cases, normalize  # noqa: E402


def main(mac_src: str) -> None:
    # Импорт маковой версии не должен писать в журнал работающей программы
    class _Quiet(logging.NullHandler):
        def __init__(self, *a, **kw):
            super().__init__()

    logging.handlers.RotatingFileHandler = _Quiet

    spec = importlib.util.spec_from_file_location("mac_local_flow", mac_src)
    mac = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mac)

    # Файлы пользователя (словарь, сниппеты, история) не читаем
    tmp = Path(tempfile.mkdtemp(prefix="lf-golden-"))
    for name in ("CONFIG_DIR", "CONFIG_PATH", "DICTIONARY_PATH", "SNIPPETS_PATH",
                 "HISTORY_PATH", "STATS_PATH", "RECOVERY_PATH", "LEARNED_PATH"):
        old = getattr(mac, name)
        setattr(mac, name, tmp if name == "CONFIG_DIR" else tmp / old.name)
    mac._UI_LANG = "ru"

    results = []
    for fn, args in cases():
        try:
            out = {"ok": normalize(getattr(mac, fn)(*args))}
        except Exception as exc:  # ошибка — тоже эталон
            out = {"error": type(exc).__name__}
        results.append({"fn": fn, "args": normalize(args), **out})

    dest = ROOT / "tests" / "golden" / "core.json"
    dest.write_text(json.dumps(results, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    print(f"Эталонов: {len(results)} -> {dest.relative_to(ROOT)}")


if __name__ == "__main__":
    main(sys.argv[1])
