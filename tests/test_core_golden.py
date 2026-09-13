"""Текстовая логика на Windows обязана отвечать так же, как на Mac."""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("LOCALFLOW_HOME", tempfile.mkdtemp(prefix="lf-test-"))
sys.path.insert(0, str(Path(__file__).parent))

from localflow import core  # noqa: E402
from corpus import normalize  # noqa: E402

GOLDEN = json.loads((Path(__file__).parent / "golden" / "core.json")
                    .read_text(encoding="utf-8"))


def _id(case):
    arg = str(case["args"][0]) if case["args"] else ""
    return f"{case['fn']}:{arg[:40]}"


@pytest.mark.parametrize("case", GOLDEN, ids=[_id(c) for c in GOLDEN])
def test_same_as_mac(case):
    core._UI_LANG = "ru"
    args = case["args"]
    try:
        got = {"ok": normalize(getattr(core, case["fn"])(*args))}
    except Exception as exc:
        got = {"error": type(exc).__name__}
    want = {k: case[k] for k in ("ok", "error") if k in case}
    assert got == want
