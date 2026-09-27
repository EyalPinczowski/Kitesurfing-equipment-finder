"""Kite models vs riding style: the reference, lookups, matching, sets and the profile."""

import pytest

from kitefinder import assemble as asm
from kitefinder import cli, kite_models, matcher
from kitefinder.assemble import BRAND_ALIASES
from kitefinder.models import Listing, OwnedItem, Profile, ValidationError
from kitefinder.sizing import quiver


def test_reference_is_well_formed():
    cat = kite_models.catalog()
    assert set(cat) <= set(BRAND_ALIASES)  # every brand is one the listings' brands map to
    for brand, models in cat.items():
        names = [m.name.lower() for m in models]
        assert len(names) == len(set(names)), brand
        for m in models:
            assert m.uses and set(m.uses) <= set(kite_models.USES), m
    for style in ("twintip", "surfboard", "foil"):  # every style has models from several brands
        assert len({n.split()[0] for n in kite_models.models_for(style)}) >= 5


@pytest.mark.parametrize(
    "brand, model, expected",
    [
        ("Duotone", "Evo SLS 2023", "Duotone Evo"),
        ("דואוטון", "Rebel D/LAB", "Duotone Rebel"),
        ("north", "orbit", "North Orbit"),
        ("Cabrinha", "Moto X 2024", "Cabrinha Moto X"),  # the longer name wins
        ("Cabrinha", "Moto", "Cabrinha Moto"),
        ("", "Carve", "North Carve"),  # no brand, but only North has a Carve
        ("", "One", "Airush One"),
        ("North", "Evo", None),  # not a North model
        ("Duotone", "", None),
        ("Mystery", "Orbit", None),
        ("Duotone", "Evolution", None),  # a word boundary, not a prefix
    ],
)
def test_lookup(brand, model, expected):
    found = kite_models.lookup(brand, model)
    assert (found.title if found else None) == expected


@pytest.mark.parametrize(
    "model, style, focus, verdict",
    [
        ("Orbit", "twintip", "", "best"),
        ("Orbit", "twintip", "bigair", "best"),
        ("Orbit", "twintip", "freestyle", "ok"),
        ("Orbit", "surfboard", "", "no"),
        ("Carve", "surfboard", "", "best"),
        ("Carve", "twintip", "", "no"),
        ("Carve", "foil", "", "best"),
        ("Pulse", "foil", "", "no"),
    ],
)
def test_suits(model, style, focus, verdict):
    assert kite_models.suits(kite_models.lookup("North", model), style, focus) == verdict


def kite(model, price=3000, size=12):
    return Listing(
        "kite", price, "North", model, size, year=2021, is_new=False, id=hash(model) % 1000
    )


def test_matching_ranks_the_right_model_first_and_flags_the_other():
    listings = [kite("Orbit"), kite("Carve"), kite("", 2900)]
    rec = quiver.recommend_set(Profile(80, 86, 12, 25, style="twintip"), [], "minimum")
    scored = {s.listing.model: s for s in matcher.match_recommendation(rec, listings)}
    assert scored["Orbit"].score > scored[""].score > scored["Carve"].score
    assert "North Orbit suits your riding (big air / freeride)" in scored["Orbit"].why
    assert (
        "⚠ North Carve is a wave / foil kite — not made for twin tip riding" in scored["Carve"].why
    )
    assert "suits" not in scored[""].why and "⚠" not in scored[""].why  # unknown model: neutral
    surf = quiver.recommend_set(Profile(80, 86, 12, 25, style="surfboard"), [], "minimum")
    scored = {s.listing.model: s for s in matcher.match_recommendation(surf, listings)}
    assert scored["Carve"].score > scored["Orbit"].score  # the other way round for wave riders


def test_focus_prefers_the_matching_kind():
    listings = [kite("Orbit"), kite("Pulse")]
    p = Profile(80, 86, 12, 25, style="twintip", discipline="freestyle")
    scored = {
        s.listing.model: s
        for s in matcher.match_recommendation(quiver.recommend_set(p, []), listings)
    }
    assert scored["Pulse"].score > scored["Orbit"].score
    assert "(you asked for freestyle)" in scored["Orbit"].why


def test_search_uses_your_style():
    found = matcher.search("kite 12m", [kite("Orbit"), kite("Carve")], style="surfboard")
    assert [s.listing.model for s in found] == ["Carve", "Orbit"]
    plain = matcher.search("kite 12m", [kite("Orbit"), kite("Carve")])
    assert all("suits" not in s.why and "⚠" not in s.why for s in plain)  # no profile: no opinion


def test_assembled_sets_leave_out_kites_for_other_riding():
    rec = quiver.recommend_set(Profile(80, 86, 12, 25, style="surfboard"), [], "one_kite")
    size = next(i.size for i in rec.items if i.type == "kite")
    only_orbit = asm.assemble(rec, [kite("Orbit", size=size)], "mixed")
    kite_pick = next(m for it, m in only_orbit.picks if it.type == "kite")
    assert kite_pick is None
    assert "Left out 1 kite listing made for other riding than yours." in only_orbit.warnings
    with_carve = asm.assemble(rec, [kite("Orbit", 2000, size), kite("Carve", 3500, size)], "mixed")
    assert next(m for it, m in with_carve.picks if it.type == "kite").listing.model == "Carve"


def test_recommendation_names_models_and_warns_about_an_owned_kite():
    p = Profile(80, 86, 12, 25, style="twintip", discipline="bigair")
    rec = quiver.recommend_set(p, [OwnedItem("kite", "Duotone", "Neo", 12)], "minimum")
    assert "Your Duotone Neo is a wave kite — not made for twin tip riding." in rec.explanation
    assert (
        "Kite models made for twin tip, big air riding: Duotone Rebel, North Orbit, "
        "Cabrinha Switchblade, Core XR, Ozone Edge, Naish Triad (and similar)." in rec.explanation
    )
    covered = quiver.recommend_set(
        Profile(80, 86, 15, 20), [OwnedItem("kite", size=11)], "one_kite"
    )
    assert "Kite models made for" not in covered.explanation  # no kite to buy: no list


def test_focus_in_the_profile(db):
    cli.run(
        [
            "profile",
            "set",
            "--weight",
            "80",
            "--waist",
            "86",
            "--wind",
            "12-25",
            "--focus",
            "bigair",
        ],
        db=db,
    )
    assert db.get_profile().discipline == "bigair"
    assert "Style: twintip (big air)" in cli.run(["profile", "show"], db=db)
    cli.run(["profile", "set", "--focus", "any"], db=db)
    assert db.get_profile().discipline == ""
    cli.run(["profile", "set", "--focus", "freestyle"], db=db)
    cli.run(["profile", "set", "--style", "surfboard"], db=db)  # the focus goes with twin tip
    assert db.get_profile().discipline == ""
    with pytest.raises(ValidationError, match="for twin tip riders"):
        cli.run(["profile", "set", "--focus", "bigair"], db=db)
    with pytest.raises(ValidationError, match="riding focus must be one of"):
        Profile(80, 86, 12, 25, discipline="racing").validate()
