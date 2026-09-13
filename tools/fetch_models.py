"""Скачать модели распознавания тем же кодом, что и программа.

    python tools/fetch_models.py base large-v3-turbo --dest models
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from localflow.engine.catalog import WHISPER_MODELS  # noqa: E402
from localflow.engine.download import download  # noqa: E402


def main() -> None:
    # Консоль Windows по умолчанию в однобайтной кодировке и падает на
    # кириллице — выводим в UTF-8, непечатное заменяем
    for stream in (sys.stdout, sys.stderr):
        if stream and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="+", choices=sorted(WHISPER_MODELS))
    ap.add_argument("--dest", default="models")
    args = ap.parse_args()
    dest = Path(args.dest)
    for name in args.models:
        model = WHISPER_MODELS[name]
        t0 = time.monotonic()
        last = [0.0]

        def progress(done, total):
            if time.monotonic() - last[0] > 5:
                last[0] = time.monotonic()
                print(f"  {model.file}: {done * 100 // total}%", flush=True)

        path = download(model, dest, progress=progress)
        print(f"{name}: {path} ({model.size_mb} МБ) за {time.monotonic() - t0:.0f} c")


if __name__ == "__main__":
    main()
