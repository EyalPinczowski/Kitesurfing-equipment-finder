import builtins
import io

import pytest
from fakes import FakeTransport, gemini_reply

from kitefinder.llm import gemini as gm
from kitefinder.llm import vision


def jpeg(w=2000, h=1500, color=(200, 30, 30)) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (w, h), color).save(out, "JPEG")
    return out.getvalue()


def png() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGBA", (50, 40), (0, 0, 255, 128)).save(out, "PNG")
    return out.getvalue()


def test_sniff_mime():
    assert vision.sniff_mime(jpeg(10, 10)) == "image/jpeg"
    assert vision.sniff_mime(png()) == "image/png"
    assert vision.sniff_mime(b"RIFF1234WEBPxxxx") == "image/webp"
    assert vision.sniff_mime(b"<html>") is None


def test_prepare_shrinks_to_jpeg():
    from PIL import Image

    img = vision.prepare_image(jpeg())
    assert img.mime_type == "image/jpeg"
    with Image.open(io.BytesIO(img.data)) as im:
        assert max(im.size) == vision.MAX_SIDE
    assert vision.prepare_image(png()).mime_type == "image/jpeg"  # RGBA converted


def test_prepare_rejects_junk_and_corrupt():
    assert vision.prepare_image(b"not an image") is None
    assert vision.prepare_image(b"\xff\xd8\xff" + b"garbage" * 10) is None


def test_prepare_without_pillow(monkeypatch):
    small = jpeg(20, 20)  # made before Pillow is hidden
    real_import = builtins.__import__

    def no_pil(name, *a, **k):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_pil)
    assert vision.prepare_image(small).data == small  # sent as is
    monkeypatch.setattr(vision, "MAX_BYTES", 10)
    assert vision.prepare_image(small) is None  # too big to send unshrunk


@pytest.mark.parametrize(
    "raw, score, flags",
    [
        ({"score": 7, "flags": ["uv_faded"], "verdict": "ok"}, 7, ["uv_faded"]),
        ({"score": 14, "flags": [], "verdict": ""}, 10, []),
        ({"score": -3, "flags": [], "verdict": ""}, 1, []),
        ({"score": True, "flags": [], "verdict": ""}, None, []),
        ({"score": "8", "flags": [], "verdict": ""}, None, []),
        ({"score": 6, "flags": ["made_up", "tear", "tear"], "verdict": ""}, 6, ["tear"]),
        ({"score": 9, "flags": ["stock_photo"], "verdict": ""}, None, ["stock_photo"]),
        ({"score": 5, "flags": ["not_equipment"], "verdict": ""}, None, ["not_equipment"]),
        (
            {"score": 9, "flags": ["looks_like_new", "repair_patch"], "verdict": ""},
            9,
            ["repair_patch"],
        ),
        ({}, None, []),
    ],
)
def test_validate(raw, score, flags):
    a = vision.validate(raw)
    assert (a.score, a.flags) == (score, flags)


def test_summary():
    assert vision.Assessment(7.5, ["uv_faded", "repair_patch"]).summary == (
        "7.5/10 — UV fading / tired cloth, repair patch or glued repair"
    )
    assert vision.Assessment(None, ["stock_photo"]).summary == (
        "condition not visible — catalogue / stock photo"
    )
    assert vision.Assessment(9, ["looks_like_new"]).summary == "9/10"


def test_assess_sends_at_most_four_shrunk_photos():
    t = FakeTransport(
        lambda body: (
            200,
            gemini_reply({"score": 8, "flags": ["dings_scratches"], "verdict": "small dings"}),
            {},
        )
    )
    client = gm.GeminiClient("K", transport=t, sleep=lambda s: None)
    result = vision.assess(
        client, [jpeg(color=(i * 40, 0, 0)) for i in range(6)] + [b"junk"], "kite North Orbit 12m"
    )
    assert (result.score, result.flags, result.verdict) == (8, ["dings_scratches"], "small dings")
    parts = t.requests[0]["body"]["contents"][0]["parts"]
    assert len(parts) == 1 + vision.MAX_IMAGES
    assert "kite North Orbit 12m" in parts[0]["text"]
    assert t.requests[0]["body"]["generationConfig"]["responseSchema"] is vision.SCHEMA


def test_assess_without_usable_photos_spends_nothing():
    t = FakeTransport(lambda body: (500, None, {}))
    client = gm.GeminiClient("K", transport=t, sleep=lambda s: None)
    assert vision.assess(client, [b"junk"]).verdict == "no usable photos"
    assert t.requests == []


def test_stops_decoding_after_four_usable_photos(monkeypatch):
    """Review regression: don't resize 12 phone photos to keep 4."""
    calls = []
    real = vision.prepare_image

    def counting(data):
        calls.append(1)
        return real(data)

    monkeypatch.setattr(vision, "prepare_image", counting)
    t = FakeTransport(
        lambda body: (200, gemini_reply({"score": 7, "flags": [], "verdict": "ok"}), {})
    )
    client = gm.GeminiClient("K", transport=t, sleep=lambda s: None)
    result = vision.assess(client, [b"junk"] + [jpeg(40, 40)] * 10)
    assert len(calls) == 1 + vision.MAX_IMAGES and result.photos_used == vision.MAX_IMAGES
