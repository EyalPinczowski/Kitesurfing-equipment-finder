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
    assert out.startswith("#1 ") and out.endswith("[set] kite 9m², board 139 cm")
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
        "• kite 8m² (7–9m²) — covers 18.5–28.5 kn\n"
        "• board twintip 140 cm (138–141 cm) — twin tip for 80 kg\n"
        "• bar 52 cm (50–55 cm) — bar for 12 m²; an adjustable-length bar can fly your whole quiver\n"
        "• harness size M/L — waist 86 cm → size M or L (between sizes: try both on)\n"
        "\n"
        "Searches will use set #1 (minimum)."
    )
    assert db.latest_recommendation().id == 1
    assert run(db, "history").endswith("[set] kite 8m², board 140 cm, bar 52 cm, harness size M/L")


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
        "• kite 9m² (8–10m²) — best single kite for 18–24 kn (usable 16.5–25.5 kn)"
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


def test_recommend_both_options_saved_and_minimum_active(db):
    run(db, *FULL_PROFILE)
    out = run(db, "recommend")
    assert "Recommendation #1 (set, minimum)" in out
    assert "Recommendation #2 (set, comfortable)" in out
    assert out.endswith("Searches will use set #1 (minimum).\nSwitch with: kitefinder use <id>")
    assert db.latest_recommendation().id == 1
    assert run(db, "use", "2") == "Searches will use set #2."
    assert db.latest_recommendation().variant == "comfortable"
    run(db, "recommend")  # a new recommendation resets the choice
    assert db.latest_recommendation().id == 3


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
    assert "The comfortable option is the same as the minimum one for your range." in out
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
