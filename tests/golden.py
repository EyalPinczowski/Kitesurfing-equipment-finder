"""Golden files: the complete text of every bot message, compared exactly.

A missing or changed message fails the test. To accept new output after reviewing it:
UPDATE_GOLDEN=1 pytest tests/test_bot.py
"""

import os
from pathlib import Path

DIR = Path(__file__).parent / "golden"


def check(name: str, text: str) -> None:
    path = DIR / f"{name}.txt"
    if os.environ.get("UPDATE_GOLDEN"):
        DIR.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return
    assert path.exists(), f"no golden file {path.name} — review the output, then UPDATE_GOLDEN=1"
    expected = path.read_text(encoding="utf-8")
    assert text == expected, f"{path.name} differs from the golden file"
