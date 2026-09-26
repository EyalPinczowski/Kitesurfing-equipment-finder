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
        "Spots: Bat Galim, Sdot Yam\n"
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
    [("kite", 9.5, "9.5m²"), ("bar", 0.5, "0.5m"), ("board", 139, "139 cm"), ("harness", 2, "2"),
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
