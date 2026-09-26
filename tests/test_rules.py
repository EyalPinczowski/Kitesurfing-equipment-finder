"""The offline rule-based extractor (used without a key or when the free quota runs out)."""

from collections import Counter
from pathlib import Path

import pytest
import yaml
from corpus import CORPUS, score

from kitefinder.llm import rules
from kitefinder.llm.extract import extract_post

HOLDOUT = yaml.safe_load(
    (Path(__file__).parent / "fixtures" / "posts_holdout.yaml").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("post", CORPUS, ids=lambda p: p["id"])
def test_rules_on_tuning_corpus(post):
    """The rules were tuned on this set, so every check must pass (a regression guard)."""
    result = extract_post(post["text"], None)
    assert [(n, d) for n, good, d in score(post, result) if not good] == []


def test_rules_on_held_out_posts():
    """Never tuned on: an honest accuracy estimate for new posts. Measured at build time:
    17/20 posts fully right, sale/not-sale 18/20, prices 20/20, types 21/23."""
    ok, total, perfect = Counter(), Counter(), 0
    for post in HOLDOUT:
        checks = score(post, extract_post(post["text"], None))
        perfect += all(good for _, good, _ in checks)
        for name, good, _ in checks:
            total[name] += 1
            ok[name] += good
    assert perfect / len(HOLDOUT) >= 0.8
    assert ok["is_sale"] / total["is_sale"] >= 0.9
    assert ok["price"] / total["price"] >= 0.95
    assert ok["type"] / total["type"] >= 0.9


@pytest.mark.parametrize(
    "text, n, types",
    [
        ('משאבה לקייט 100 ש"ח', 1, ["other"]),
        ("סרפבורד לקייט 5'6 ב-1600 ש\"ח", 1, ["board"]),
        ("kite surfboard 5'8 1900 NIS", 1, ["board"]),
        ('פליסרפר 11 מטר קייט פויל 4200 ש"ח', 1, ["kite"]),
        ('פויל F-One תורן 75 כנף 1800, 3500 ש"ח', 1, ["foil"]),
        ('מוכר ציוד קייט: טרפז מידה M ב-600 ש"ח', 1, ["harness"]),
        ("מוכר 2 קייטים: 9 מטר ב-2400 ו-12 מטר ב-2900", 2, ["kite", "kite"]),
        ("Duotone Evo 10m 2021, 3,400 NIS", 1, ["kite"]),
        ("the edge of the board 139x42 1200 NIS", 1, ["board"]),  # "edge" alone is not a kite
    ],
)
def test_item_finding_rules(text, n, types):
    raw = rules.extract_raw(text)
    assert [i["type"] for i in raw["items"]] == types and len(raw["items"]) == n


@pytest.mark.parametrize(
    "text, reason",
    [("מחפש קייט 9 מטר", "looking to buy"), ("WTB twin tip", "looking to buy"),
     ("קורס קייט ב-1200 ש\"ח", "lesson / trip / course"), ("וואו איזה יום", "no gear mentioned"),
     ("קייט 12 מטר", "no sale wording or price")],
)  # fmt: skip
def test_not_sale_reasons(text, reason):
    raw = rules.extract_raw(text)
    assert raw["is_sale_post"] is False and raw["not_sale_reason"] == reason


def test_single_price_with_several_items_is_a_bundle():
    raw = rules.extract_raw('קייט 12 מטר ובר, ביחד 4500 ש"ח')
    assert raw["bundle_price_ils"] == 4500 and all(i["price_ils"] is None for i in raw["items"])


def test_single_item_gets_the_only_price():
    raw = rules.extract_raw("למכירה קייט 9 מטר. מחיר 2000")
    assert raw["items"][0]["price_ils"] == 2000 and raw["bundle_price_ils"] is None


def test_new_used_and_sold_words():
    assert rules.extract_raw('קייט 9 מטר חדש באריזה 5000 ש"ח')["items"][0]["is_new"] is True
    assert rules.extract_raw('קייט 9 מטר כמו חדש 3000 ש"ח')["items"][0]["is_new"] is False
    assert rules.extract_raw("נמכר! קייט 9 מטר")["items"][0]["sold"] is True


# --- regressions from the step-3 review ------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["מוכר קייט North Orbit 12 מטר ב-4500, כבר לא צריך", 'קייט 9 מטר ב-2000 ש"ח, יש שבר קטן בסטרט'],
)
def test_short_hebrew_word_bar_not_found_inside_other_words(text):
    """'כבר' (already) and 'שבר' (broken) are not a bar."""
    assert [i["type"] for i in rules.extract_raw(text)["items"]] == ["kite"]


@pytest.mark.parametrize("text", ['מוכר הבר שלי 1000 ש"ח', 'קייט 9 מטר והבר, 3000 ש"ח'])
def test_bar_with_allowed_prefixes_still_found(text):
    assert "bar" in [i["type"] for i in rules.extract_raw(text)["items"]]


@pytest.mark.parametrize(
    "text, year",
    [('מוכר סרפבורד 6\' ב 2000 ש"ח', None), ("סרפבורד 5'10 ב-2500 ₪", None), ("קייט 9 מטר ב-₪2020", None),
     ("קייט 9 מטר '21 ב-2000 ש\"ח", 2021), ('קייט 9 מטר 2019 ב 2000 ש"ח', 2019)],
)  # fmt: skip
def test_prices_and_feet_are_not_years(text, year):
    assert rules.extract_raw(text)["items"][0]["year"] == year


@pytest.mark.parametrize(
    "text, cm", [("סרפבורד 6' ב-2000 ש\"ח", 183), ("surfboard 5'8 1900 NIS", 173)]
)
def test_whole_and_partial_feet(text, cm):
    assert rules.extract_raw(text)["items"][0]["size"] == cm
