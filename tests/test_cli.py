import pytest

from kitefinder import cli
from kitefinder.models import RecItem, Recommendation, ValidationError


def run(db, *args):
    return cli.run(list(args), db=db)


FULL_PROFILE = [
    "profile", "set", "--weight", "80", "--waist", "86", "--wind", "12-25",
    "--skill", "intermediate", "--style", "twintip", "--spots", "Bat Galim, Sdot Yam",
    "--budget", "8000", "--condition", "used", "--travel-km", "120", "--home", "Haifa",
]  # fmt: skip


def test_profile_set_and_show_full_output(db):
    out = run(db, *FULL_PROFILE)
    expected = (
        "Weight: 80 kg\n"
        "Hip/waist: 86 cm\n"
        "Wind range: 12–25 kn\n"
        "Skill: intermediate\n"
        "Style: twintip\n"
        "Spots: Bat Galim, Sdot Yam / Caesarea\n"
        "Gusty spots: no\n"
        "Budget: ₪8,000\n"
        "New/used: used\n"
        "Travel: 120 km\n"
        "Minimum year: —\n"
        "Home: Haifa"
    )
    assert out == "Profile saved.\n" + expected
    assert run(db, "profile", "show") == expected


def test_profile_partial_update(db):
    run(db, *FULL_PROFILE)
    run(db, "profile", "set", "--weight", "76.5", "--wind", "15–30")
    p = db.get_profile()
    assert (p.weight_kg, p.wind_min_kn, p.wind_max_kn, p.home_location) == (76.5, 15, 30, "Haifa")


def test_profile_first_time_requires_basics(db):
    with pytest.raises(ValidationError, match="--waist, --wind"):
        run(db, "profile", "set", "--weight", "80")


def test_profile_show_empty(db):
    assert "No profile yet" in run(db, "profile", "show")


def test_profile_show_minimal_uses_dashes(db):
    run(db, "profile", "set", "--weight", "70", "--waist", "80", "--wind", "10 20")
    out = run(db, "profile", "show")
    assert "Spots: —" in out and "Budget: —" in out and "Travel: —" in out and "Home: —" in out


@pytest.mark.parametrize(
    "text, expected",
    [("12-25", (12, 25)), ("12 - 25", (12, 25)), ("12–25", (12, 25)), ("8.5 18", (8.5, 18))],
)
def test_parse_wind_range(text, expected):
    assert cli.parse_wind_range(text) == expected


@pytest.mark.parametrize("text", ["12", "a-b", "1-2-3"])
def test_parse_wind_range_bad(text):
    with pytest.raises(ValidationError):
        cli.parse_wind_range(text)


def test_gear_add_list_rm(db):
    assert run(db, "gear", "list") == "No gear yet."
    out = run(db, "gear", "add", "--type", "kite", "--brand", "North", "--model", "Orbit",
              "--size", "12", "--year", "2022")  # fmt: skip
    assert out == "Added #1 kite: North Orbit 12m² 2022"
    run(db, "gear", "add", "--type", "board", "--size", "138")
    run(db, "gear", "add", "--type", "harness")
    assert run(db, "gear", "list") == (
        "#2 board: (no brand) 138 cm\n#3 harness: (no brand) ?\n#1 kite: North Orbit 12m² 2022"
    )
    assert run(db, "gear", "rm", "2") == "Removed #2"
    assert run(db, "gear", "rm", "2") == "No gear with id 2"


def test_sites_commands(db):
    assert run(db, "sites", "list") == "No sites yet."
    assert run(db, "sites", "add", "shop.co.il/kites/") == "Added site #1"
    assert run(db, "sites", "add", "https://SHOP.co.il/kites") == "Already listed site #1"
    assert run(db, "sites", "list") == "#1 https://shop.co.il/kites"
    assert run(db, "sites", "rm", "1") == "Removed site #1"
    assert run(db, "sites", "rm", "1") == "No site with id 1"


def test_history_and_marks(db, profile):
    assert run(db, "history") == "No recommendations yet."
    rec = Recommendation(profile=profile, items=[RecItem("kite", 9), RecItem("board", 139)])
    db.save_recommendation(rec)
    out = run(db, "history")
    assert out.startswith("#1 ") and out.endswith("[set, minimum] kite 9m², board 139 cm")
    item_id = rec.items[0].id
    assert run(db, "favorites") == "No favorites yet."
    assert run(db, "fav", "rec_item", str(item_id)) == f"Marked rec_item #{item_id} as favorite"
    assert run(db, "favorites") == f"rec_item #{item_id}"
    assert (
        run(db, "dismiss", "rec_item", str(item_id)) == f"Marked rec_item #{item_id} as dismissed"
    )
    assert run(db, "favorites") == "No favorites yet."
    assert run(db, "unmark", "rec_item", str(item_id)) == "Mark removed"
    assert run(db, "unmark", "rec_item", str(item_id)) == "Nothing to remove"


def test_backup_command(db, tmp_path):
    out = run(db, "backup", str(tmp_path))
    assert out.startswith("Backup written to ") and out.endswith(".db")


def test_main_reports_validation_errors(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("KITEFINDER_DATA_DIR", str(tmp_path))
    assert cli.main(["profile", "set", "--weight", "10", "--waist", "80", "--wind", "10-20"]) == 2
    assert "weight must be between" in capsys.readouterr().err
    assert cli.main(["gear", "list"]) == 0
    assert "No gear yet." in capsys.readouterr().out


@pytest.mark.parametrize(
    "t, size, expected",
    [("kite", 9.5, "9.5m²"), ("bar", 50, "50 cm"), ("foil", 1600, "1600 cm²"), ("board", 139, "139 cm"), ("harness", 2, "2"),
     ("kite", None, "?")],
)  # fmt: skip
def test_fmt_size(t, size, expected):
    assert cli.fmt_size(t, size) == expected


def test_main_reports_permission_errors_with_termux_hint(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("KITEFINDER_DATA_DIR", str(tmp_path))

    def deny(self, dest):
        raise PermissionError(13, "Permission denied", "/sdcard/Download")

    monkeypatch.setattr("kitefinder.db.Database.backup", deny)
    assert cli.main(["backup"]) == 1
    assert "termux-setup-storage" in capsys.readouterr().err


def test_cli_loads_seed_sites_from_config(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("KITEFINDER_DATA_DIR", str(tmp_path))
    assert cli.main(["sites", "list"]) == 0
    out = capsys.readouterr().out
    assert "#1 https://kitelab.co.il" in out
    assert "yamitysb.co.il/product-category/surf/%d7%a7" in out
    assert "https://www.iks-surf.co.il/en/kitesurf-equipment" in out
    assert out.count("\n") == 6


# --- step 2: recommend ------------------------------------------------------------------------


def test_recommend_needs_profile(db):
    with pytest.raises(ValidationError, match="profile first"):
        run(db, "recommend")


def test_recommend_set_full_output_and_saved(db):
    run(db, *FULL_PROFILE)
    run(db, "gear", "add", "--type", "kite", "--brand", "North", "--model", "Orbit", "--size", "12")
    out = run(db, "recommend", "--option", "minimum")
    assert out == (
        "Recommendation #1 (set, minimum)\n"
        "Set for 80 kg, 12–25 kn, twintip, intermediate.\n"
        "Option: minimum — fewest kites.\n"
        "Kite quiver: 12 m² (12.5–19 kn) [owned], 8 m² (18.5–28.5 kn).\n"
        "Your 12 m² kite is slightly underpowered between 12 and 12.5 kn.\n"
        "Using your North Orbit 12 m² kite for 12.5–19 kn.\n"
        "To look for:\n"
        "• kite 8m² (7–9m²) — covers 18.5–28.5 kn · ~₪2,700\n"
        "• board twintip 140 cm (138–141 cm) — twin tip for 80 kg · ~₪1,400\n"
        "• bar 52 cm (50–55 cm) — bar for 12 m²; an adjustable-length bar can fly your whole quiver · ~₪1,300\n"
        "• harness size M/L — waist 86 cm → size M or L (between sizes: try both on) · ~₪700\n"
        "Estimated cost: ~₪6,100 used (typical Israeli prices, not live listings)\n"
        "✓ Fits your ₪8,000 budget.\n"
        "\n"
        "Searches will use set #1 (minimum)."
    )
    assert db.latest_recommendation().id == 1
    assert run(db, "history").endswith(
        "[set, minimum] kite 8m², board 140 cm, bar 52 cm, harness size M/L · ~₪6,100 used"
    )


def test_recommend_nothing_to_buy(db):
    run(db, *FULL_PROFILE)
    for args in (
        ["--type", "kite", "--size", "13"],
        ["--type", "kite", "--size", "9"],
        ["--type", "board", "--size", "139", "--subtype", "twintip"],
        ["--type", "bar"],
        ["--type", "harness"],
    ):
        run(db, "gear", "add", *args)
    out = run(db, "recommend")
    assert "Nothing to buy — your gear covers it." in out


def test_recommend_single_item(db):
    run(db, *FULL_PROFILE)
    out = run(db, "recommend", "--item", "kite", "--wind", "18-24")
    assert out == (
        "Recommendation #1 (single)\n"
        "best single kite for 18–24 kn (usable 16.5–25.5 kn)\n"
        "To look for:\n"
        "• kite 9m² (8–10m²) — best single kite for 18–24 kn (usable 16.5–25.5 kn) · ~₪2,800\n"
        "Estimated cost: ~₪2,800 used (typical Israeli prices, not live listings)"
    )
    assert db.latest_recommendation() is None  # singles never replace the active set


def test_recommend_wind_requires_item(db):
    run(db, *FULL_PROFILE)
    with pytest.raises(ValidationError, match="--item"):
        run(db, "recommend", "--wind", "15-20")


def test_gear_subtype(db):
    out = run(db, "gear", "add", "--type", "board", "--subtype", "foilboard", "--size", "90")
    assert out.startswith("Added #1 board")
    assert db.list_owned()[0].subtype == "foilboard"
    with pytest.raises(ValidationError, match="subtype for kite"):
        run(db, "gear", "add", "--type", "kite", "--subtype", "twintip")


def test_recommend_bad_wind_is_friendly_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("KITEFINDER_DATA_DIR", str(tmp_path))
    cli.main(["profile", "set", "--weight", "80", "--waist", "86", "--wind", "12-25"])
    assert cli.main(["recommend", "--item", "kite", "--wind", "0-0"]) == 2
    assert "wind range must be" in capsys.readouterr().err


# --- step 2b: areas instead of wind speed -------------------------------------------------------


def test_first_setup_with_areas_only(db):
    out = run(
        db, "profile", "set", "--weight", "80", "--waist", "86", "--areas", "בת גלים, Sdot Yam"
    )
    assert out == (
        "Profile saved.\n"
        "Weight: 80 kg\n"
        "Hip/waist: 86 cm\n"
        "Wind range: 10–35 kn (from your areas, all year)\n"
        "Skill: intermediate\n"
        "Style: twintip\n"
        "Spots: Bat Galim, Sdot Yam / Caesarea\n"
        "Gusty spots: no\n"
        "Budget: —\n"
        "New/used: both\n"
        "Travel: —\n"
        "Minimum year: —\n"
        "Home: —"
    )
    p = db.get_profile()
    assert (p.wind_source, p.season) == ("areas", "all")


def test_first_setup_needs_wind_or_areas(db):
    with pytest.raises(ValidationError, match="--wind or --areas"):
        run(db, "profile", "set", "--weight", "80", "--waist", "86")


def test_season_and_region(db):
    run(
        db,
        "profile",
        "set",
        "--weight",
        "80",
        "--waist",
        "86",
        "--areas",
        "north",
        "--season",
        "summer",
    )
    p = db.get_profile()
    assert (p.wind_min_kn, p.wind_max_kn, p.season) == (10, 18, "summer")
    run(db, "profile", "set", "--season", "winter")  # re-derived from the saved areas
    p = db.get_profile()
    assert (p.wind_min_kn, p.wind_max_kn) == (15, 35)
    assert "(from your areas, winter)" in run(db, "profile", "show")


def test_manual_wind_wins_until_switched_back(db):
    run(db, "profile", "set", "--weight", "80", "--waist", "86", "--areas", "Bat Galim")
    run(db, "profile", "set", "--wind", "14-22")
    assert db.get_profile().wind_source == "manual"
    out = run(db, "profile", "set", "--areas", "Eilat")
    assert "Wind range kept at 14–22 kn (set by hand). Use --wind areas" in out
    p = db.get_profile()
    assert (p.wind_min_kn, p.wind_max_kn, p.spots) == (14, 22, ["Eilat North Beach"])
    run(db, "profile", "set", "--wind", "areas")
    p = db.get_profile()
    assert (p.wind_source, p.wind_min_kn, p.wind_max_kn) == ("areas", 12, 25)


def test_wind_areas_without_any_areas(db):
    run(db, *FULL_PROFILE[:8])  # weight, waist, wind only
    with pytest.raises(ValidationError, match="--wind areas needs --areas"):
        run(db, "profile", "set", "--wind", "areas")


def test_gusty_from_areas_and_manual_override(db):
    run(db, "profile", "set", "--weight", "80", "--waist", "86", "--areas", "Tel Aviv")
    assert db.get_profile().gusty is True
    assert "Gusty spots: yes" in run(db, "profile", "show")
    run(db, "profile", "set", "--no-gusty")
    assert db.get_profile().gusty is False
    run(db, "profile", "set", "--areas", "Kinneret", "--no-gusty")
    assert db.get_profile().gusty is False  # explicit flag beats the area's default


def test_unknown_area_is_friendly_error(db):
    with pytest.raises(ValidationError, match="did you mean Bat Galim"):
        run(db, "profile", "set", "--weight", "80", "--waist", "86", "--areas", "bat galm")
    assert db.get_profile() is None


def test_areas_command(db):
    out = run(db, "areas")
    assert out.startswith("North (צפון):\n  Achziv / Nahariya (אכזיב)")


# --- step 2b: minimum vs comfortable, active set, restore ---------------------------------------


def test_recommend_all_options_without_budget_minimum_active(db):
    run(db, "profile", "set", "--weight", "80", "--waist", "86", "--wind", "12-25")
    out = run(db, "recommend")
    assert "Recommendation #1 (set, minimum)" in out
    assert "Recommendation #2 (set, comfortable)" in out
    assert "Recommendation #3 (set, one_kite)" in out
    assert "budget" not in out
    # condition "both": used prices per item plus the new total
    assert "Estimated cost: ~₪9,500 used · ~₪21,000 new" in out
    assert "Estimated cost: ~₪12,300 used · ~₪27,200 new" in out
    assert "Estimated cost: ~₪6,200 used · ~₪13,600 new" in out
    assert out.endswith("Searches will use set #1 (minimum).\nSwitch with: kitefinder use <id>")
    assert run(db, "use", "2") == "Searches will use set #2."
    assert db.latest_recommendation().variant == "comfortable"
    run(db, "recommend")  # a new recommendation resets the choice
    assert db.latest_recommendation().id == 4


def test_recommend_all_options_with_budget_marks_and_activates_best_fit(db):
    run(db, *FULL_PROFILE)  # budget ₪8,000, used gear
    out = run(db, "recommend")
    assert out.count("✗ ₪1,500 over your ₪8,000 budget.") == 1  # minimum ₪9,500
    assert "✗ ₪4,300 over your ₪8,000 budget." in out  # comfortable ₪12,300
    assert out.count("✓ Fits your ₪8,000 budget.") == 1  # one kite ₪6,200
    assert "Searches will use set #3 (one_kite)." in out
    assert db.latest_recommendation().variant == "one_kite"


def test_recommend_identical_options_shown_once(db):
    run(
        db,
        "profile",
        "set",
        "--weight",
        "110",
        "--waist",
        "86",
        "--wind",
        "8-15",
        "--skill",
        "beginner",
    )
    out = run(db, "recommend")
    assert out.count("Recommendation #") == 1
    assert "The comfortable quiver is the same as the minimum quiver for your range." in out
    assert "The one-kite quiver is the same as the minimum quiver for your range." in out
    assert "Switch with" not in out
    assert len(db.list_recommendations()) == 1


def test_recommend_single_option_becomes_active(db):
    run(db, *FULL_PROFILE)
    run(db, "recommend", "--option", "minimum")
    out = run(db, "recommend", "--option", "comfortable")
    assert out.endswith("Searches will use set #2 (comfortable).")
    assert db.latest_recommendation().id == 2


def test_use_rejects_singles_and_unknown(db):
    run(db, *FULL_PROFILE)
    run(db, "recommend", "--item", "kite")
    with pytest.raises(ValidationError, match="no saved set with id 1"):
        run(db, "use", "1")
    with pytest.raises(ValidationError, match="no saved set with id 99"):
        run(db, "use", "99")


def test_restore_command(db, tmp_path):
    run(db, *FULL_PROFILE)
    backup = db.backup(tmp_path / "b.db")
    run(db, "profile", "set", "--weight", "95")
    out = run(db, "restore", str(backup))
    assert out.startswith(f"Restored {backup}. The previous database was saved to ")
    assert db.get_profile().weight_kg == 80


def test_clearing_areas(db):
    run(db, "profile", "set", "--weight", "80", "--waist", "86", "--areas", "Bat Galim")
    out = run(db, "profile", "set", "--areas", "")
    assert "Areas cleared; keeping 10–35 kn as your wind range." in out
    p = db.get_profile()
    assert (p.spots, p.wind_source, p.wind_min_kn, p.wind_max_kn) == ([], "manual", 10, 35)
    out = run(db, "profile", "set", "--spots", "")  # already empty, manual: no note
    assert "Areas cleared" not in out and "Spots: —" in out


# --- one-kite option and budget ----------------------------------------------------------------


def test_recommend_one_kite_option(db):
    run(db, "profile", "set", "--weight", "80", "--waist", "86", "--wind", "12-25")
    out = run(db, "recommend", "--option", "one_kite")
    assert out.startswith("Recommendation #1 (set, one_kite)\n")
    assert "One kite: underpowered below 16.5 kn." in out
    assert out.endswith("Searches will use set #1 (one_kite).")


def test_under_full_output_with_step_up(db):
    run(db, "profile", "set", "--weight", "80", "--waist", "86", "--wind", "12-25")
    out = run(db, "recommend", "--under", "9000")
    assert out == (
        "Best set within ₪9,000: one-kite quiver, used — ~₪6,200.\n"
        "\n"
        "Recommendation #1 (set, one_kite)\n"
        "Set for 80 kg, 12–25 kn, twintip, intermediate.\n"
        "Option: one kite — simplest and cheapest.\n"
        "Kite quiver: 9 m² (16.5–25.5 kn).\n"
        "One kite: underpowered below 16.5 kn.\n"
        "To look for:\n"
        "• kite 9m² (8–10m²) — covers 16.5–25.5 kn · ~₪2,800\n"
        "• board twintip 140 cm (138–141 cm) — twin tip for 80 kg · ~₪1,400\n"
        "• bar 48 cm (45–50 cm) — bar for 9 m² · ~₪1,300\n"
        "• harness size M/L — waist 86 cm → size M or L (between sizes: try both on) · ~₪700\n"
        "Estimated cost: ~₪6,200 used (typical Israeli prices, not live listings)\n"
        "✓ Fits your ₪9,000 budget.\n"
        "For ₪500 more: minimum quiver, used (~₪9,500)."
    )
    rec = db.latest_recommendation()
    assert (rec.id, rec.budget_ils, rec.price_condition) == (1, 9000, "used")


def test_under_uses_profile_budget_and_picks_new_when_affordable(db):
    run(
        db,
        "profile",
        "set",
        "--weight",
        "80",
        "--waist",
        "86",
        "--wind",
        "12-25",
        "--budget",
        "30000",
    )
    out = run(db, "recommend", "--under")
    assert out.startswith("Best set within ₪30,000: comfortable quiver, new — ~₪27,200.")
    assert "For ₪" not in out  # already the best option


def test_under_nothing_fits(db):
    run(db, *FULL_PROFILE)
    run(db, "recommend")  # set #1..#3; #3 (one kite) is active
    out = run(db, "recommend", "--under", "4000")
    assert out.startswith(
        "Nothing fits ₪4,000. The cheapest rideable set (one-kite quiver, used) is ~₪6,200, ₪2,200 over."
    )
    assert "✗ ₪2,200 over your ₪4,000 budget." in out
    assert db.latest_recommendation().id == 3  # a set that doesn't fit never becomes active


def test_under_errors(db):
    run(db, "profile", "set", "--weight", "80", "--waist", "86", "--wind", "12-25")
    with pytest.raises(ValidationError, match="set a budget"):
        run(db, "recommend", "--under")
    with pytest.raises(ValidationError, match="drop --item/--option"):
        run(db, "recommend", "--under", "9000", "--option", "minimum")
    with pytest.raises(ValidationError, match="drop --item/--option"):
        run(db, "recommend", "--under", "9000", "--item", "kite")


def test_new_only_rider_sees_new_prices(db):
    run(
        db,
        "profile",
        "set",
        "--weight",
        "80",
        "--waist",
        "86",
        "--wind",
        "12-25",
        "--condition",
        "new",
    )
    out = run(db, "recommend", "--option", "minimum")
    assert "Estimated cost: ~₪21,000 new (typical" in out


def test_under_zero_budget_is_a_real_budget(db):
    run(db, "profile", "set", "--weight", "80", "--waist", "86", "--wind", "12-25", "--budget", "0")
    assert run(db, "recommend", "--under").startswith("Nothing fits ₪0.")
    run(db, "profile", "set", "--budget", "9000")
    assert run(db, "recommend", "--under", "0").startswith("Nothing fits ₪0.")  # explicit 0 wins
    with pytest.raises(ValidationError, match="negative"):
        run(db, "recommend", "--under", "-5")


# --- assemble from listings ---------------------------------------------------------------------


def _market(db):
    from kitefinder.models import Listing

    for listing in [
        Listing("kite", 3200, "Duotone", "Evo", 13, year=2021, is_new=False, location="Herzliya", source="facebook",
                url="https://fb.com/p/1", seller="Dan"),
        Listing("kite", 2600, "North", "Orbit", 13, is_new=False, location="Haifa", source="yad2",
                url="https://yad2.co.il/i/2", seller="Noa"),
        Listing("kite", 2500, "North", "Reach", 9, source="yad2", url="https://yad2.co.il/i/3", seller="Noa"),
        Listing("kite", 2300, "דואוטון", "Rebel", 9, source="kitelab.co.il",
                url="https://kitelab.co.il/p/4", seller="Kitelab"),
        Listing("bar", 1100, "North", seller="Noa", source="yad2"),
        Listing("bar", 1300, "Duotone", size=52, seller="Kitelab", source="kitelab.co.il"),
        Listing("board", 1300, "Cabrinha", size=139, subtype="twintip", seller="Eli", source="facebook"),
        Listing("harness", 600, "ION", size_label="M", seller="Gal", source="facebook"),
    ]:  # fmt: skip
        db.add_listing(listing)


def test_assemble_no_listings(db):
    run(db, "profile", "set", "--weight", "80", "--waist", "86", "--wind", "12-25")
    assert run(db, "assemble") == "No listings collected yet — they arrive once the collectors run."


def test_assemble_needs_profile_and_set(db):
    with pytest.raises(ValidationError, match="profile first"):
        run(db, "assemble")
    run(db, "profile", "set", "--weight", "80", "--waist", "86", "--wind", "12-25")
    _market(db)
    with pytest.raises(ValidationError, match="no saved set"):
        run(db, "assemble")


def test_assemble_mixed_full_output(db):
    run(db, "profile", "set", "--weight", "80", "--waist", "86", "--wind", "12-25")
    _market(db)
    run(db, "recommend", "--option", "minimum")
    assert run(db, "assemble") == (
        "Cheapest set from listings (mixed brands) for recommendation #1 (minimum): ₪7,900 from 4 sellers.\n"
        "• kite 13m² → North Orbit 13m² · ₪2,600 used · Haifa · yad2 · https://yad2.co.il/i/2\n"
        "• kite 9m² → Duotone Rebel 9m² · ₪2,300 condition not stated · location unknown · kitelab.co.il · https://kitelab.co.il/p/4\n"
        "• board twintip 140 cm → Cabrinha 139 cm · ₪1,300 condition not stated · location unknown · facebook\n"
        "• bar 52 cm → North · ₪1,100 condition not stated · location unknown · yad2\n"
        "• harness M/L → ION size M · ₪600 condition not stated · location unknown · facebook\n"
        "⚠ Kites from different brands (Duotone, North): one bar may not fly them all — check compatibility or try --brands kites_bar.\n"
        "⚠ Size not stated for: bar — ask the seller.\n"
        "With kites and bar from one brand (North): ₪8,100 (+₪200) — kitefinder assemble --brands kites_bar"
    )


def test_assemble_kites_bar_and_same(db):
    run(db, "profile", "set", "--weight", "80", "--waist", "86", "--wind", "12-25")
    _market(db)
    run(db, "recommend", "--option", "minimum")
    out = run(db, "assemble", "--brands", "kites_bar")
    assert out.startswith(
        "Cheapest set from listings (North kites and bar) for recommendation #1 (minimum): ₪8,100 from 3 sellers."
    )
    assert "With kites and bar from one brand" not in out
    out = run(db, "assemble", "--brands", "same")
    assert out.startswith(
        "Partial set from listings (all North) for recommendation #1 (minimum): 3 of 5 items found"
    )
    assert "Missing: board twintip 140 cm, harness M/L (still looking)." in out


def test_assemble_specific_set_and_budget_line(db):
    run(db, *FULL_PROFILE)  # budget ₪8,000
    _market(db)
    run(db, "recommend")  # #1 minimum, #2 comfortable, #3 one kite (active)
    out = run(db, "assemble", "--set", "1")
    assert "for recommendation #1 (minimum): ₪7,900" in out  # used-only rider: same listings
    assert "✓ Fits your ₪8,000 budget." in out


def test_assemble_under(db):
    run(
        db,
        "profile",
        "set",
        "--weight",
        "80",
        "--waist",
        "86",
        "--wind",
        "12-25",
        "--budget",
        "7000",
    )
    _market(db)
    out = run(db, "assemble", "--under")
    assert out.startswith(
        "Cheapest set from listings (mixed brands) for recommendation #1 (one_kite): ₪5,300"
    )
    assert out.splitlines()[-1] == "✓ Fits your ₪7,000 budget."
    out = run(db, "assemble", "--under", "9000")
    assert "recommendation #2 (minimum): ₪7,900" in out
    with pytest.raises(ValidationError, match="drop --set"):
        run(db, "assemble", "--under", "9000", "--set", "1")


def test_assemble_under_needs_budget(db):
    run(db, "profile", "set", "--weight", "80", "--waist", "86", "--wind", "12-25")
    _market(db)
    with pytest.raises(ValidationError, match="set a budget"):
        run(db, "assemble", "--under")


def test_assemble_under_saves_budget_prices_and_activates(db):
    run(
        db,
        "profile",
        "set",
        "--weight",
        "80",
        "--waist",
        "86",
        "--wind",
        "12-25",
        "--budget",
        "7000",
    )
    _market(db)
    run(db, "assemble", "--under")
    rec = db.latest_recommendation()
    assert (rec.id, rec.variant, rec.budget_ils) == (1, "one_kite", 7000)
    assert all(i.est_price_ils for i in rec.items)
    assert "~₪" in run(db, "history")
    with pytest.raises(ValidationError, match="negative"):
        run(db, "assemble", "--under", "-500")


def test_profile_min_year(db):
    run(
        db,
        "profile",
        "set",
        "--weight",
        "80",
        "--waist",
        "86",
        "--wind",
        "12-25",
        "--min-year",
        "2019",
    )
    assert "Minimum year: 2019" in run(db, "profile", "show")
    run(db, "profile", "set", "--min-year", "0")
    assert db.get_profile().min_year is None
    with pytest.raises(ValidationError, match="minimum year"):
        run(db, "profile", "set", "--min-year", "1980")


def test_assemble_min_year_and_unpriced_output(db):
    from kitefinder.models import Listing

    run(
        db,
        "profile",
        "set",
        "--weight",
        "80",
        "--waist",
        "86",
        "--wind",
        "15-20",
        "--min-year",
        "2019",
    )
    for listing in [
        Listing("kite", 1900, "North", "Orbit", 10, year=2015, source="yad2", url="u-old"),
        Listing("kite", None, "North", "Orbit", 10, year=2021, source="facebook", url="u-new"),
        Listing("board", 1300, "Cabrinha", size=139, year=2020, source="facebook", url="u-b"),
        Listing("bar", 1000, "North", year=2020, source="yad2", url="u-bar"),
        Listing("harness", 600, "ION", size_label="M", source="facebook", url="u-h"),
    ]:
        db.add_listing(listing)
    run(db, "recommend", "--option", "one_kite")
    out = run(db, "assemble")
    assert out.startswith(
        "Cheapest set from listings (mixed brands, 2019 or newer) for recommendation #1 (one_kite): ~₪5,800"
    )
    assert (
        "• kite 10m² → North Orbit 10m² 2021 · price not stated (typical ~₪2,900) condition not stated"
        in out
    )
    assert "u-old" not in out
    assert "⚠ Year not stated for: harness — ask the seller." in out
    assert (
        "⚠ No price stated for: kite — the total uses typical prices for them; ask the seller."
        in out
    )
    out = run(db, "assemble", "--min-year", "0")  # override: any year
    assert "2019 or newer" not in out and "u-old" in out
    with pytest.raises(ValidationError, match="minimum year"):
        run(db, "assemble", "--min-year", "1900")


def test_assemble_under_with_unpriced_items_is_not_a_confirmed_fit(db):
    """Review regression: a total that includes guessed prices is labelled and not activated."""
    from kitefinder.models import Listing

    run(
        db,
        "profile",
        "set",
        "--weight",
        "80",
        "--waist",
        "86",
        "--wind",
        "15-20",
        "--budget",
        "9000",
    )
    for listing in [
        Listing("kite", None, "North", "Orbit", 10, source="facebook", url="k"),
        Listing("board", 1300, "Cabrinha", size=140, source="facebook", url="b"),
        Listing("bar", 1000, "North", source="yad2", url="bar"),
        Listing("harness", 600, "ION", size_label="M", source="facebook", url="h"),
    ]:
        db.add_listing(listing)
    run(db, "recommend", "--option", "minimum")  # set #1 becomes active
    out = run(db, "assemble", "--under")
    assert "≈ Fits your ₪9,000 budget at typical prices — confirm with the sellers." in out
    assert db.latest_recommendation().id == 1  # the guessed fit did not replace the active set


def test_brand_alternative_line_marks_estimates(db):
    from kitefinder.models import Listing

    run(db, "profile", "set", "--weight", "80", "--waist", "86", "--wind", "15-20")
    for listing in [
        Listing("kite", 2000, "Duotone", "Rebel", 10, source="yad2", url="k1"),
        Listing("kite", None, "North", "Orbit", 10, source="yad2", url="k2"),
        Listing("bar", 900, "North", source="yad2", url="bar"),
        Listing("board", 1300, "Cabrinha", size=140, source="facebook", url="b"),
        Listing("harness", 600, "ION", size_label="M", source="facebook", url="h"),
    ]:
        db.add_listing(listing)
    run(db, "recommend", "--option", "one_kite")
    out = run(db, "assemble")
    assert "Bar (North) and kite (Duotone) brands differ" in out
    assert "With kites and bar from one brand (North): ~₪" in out
