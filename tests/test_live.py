"""Live checks against the real Gemini API (run: pytest -m live; needs GEMINI_API_KEY).

Family 1 on the real model: every labelled post (tuning + held-out) must come back with the
right sale/not-sale decision, 100% correct item types, and sizes within tolerance. With
KITEFINDER_RECORD=1 the answers are also saved under tests/fixtures/recordings/ for review.
"""

import json
import os
from pathlib import Path

import pytest
import yaml
from corpus import CORPUS, score

pytestmark = pytest.mark.live

HOLDOUT = yaml.safe_load(
    (Path(__file__).parent / "fixtures" / "posts_holdout.yaml").read_text(encoding="utf-8")
)
RECORD_DIR = Path(__file__).parent / "fixtures" / "recordings"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        pytest.skip("GEMINI_API_KEY not set")
    from kitefinder.db import Database
    from kitefinder.llm.gemini import GeminiClient

    db = Database(tmp_path_factory.mktemp("live") / "live.db")
    return GeminiClient(key, db=db, rpd=1000)


@pytest.mark.parametrize("post", CORPUS + HOLDOUT, ids=lambda p: p["id"])
def test_real_model_on_labelled_posts(client, post):
    from kitefinder.llm.extract import extract_post

    result = extract_post(post["text"], client)
    assert result.method == "gemini", result.flags
    checks = score(post, result)
    if os.environ.get("KITEFINDER_RECORD"):
        RECORD_DIR.mkdir(exist_ok=True)
        (RECORD_DIR / f"{post['id']}.json").write_text(
            json.dumps(
                [vars(x) for x in result.listings], ensure_ascii=False, indent=1, default=str
            ),
            encoding="utf-8",
        )
    must = {"is_sale", "item_count", "type", "size"}
    assert [(n, d) for n, good, d in checks if n in must and not good] == []
