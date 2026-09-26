import pytest

from kitefinder.db import Database
from kitefinder.models import Profile


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


@pytest.fixture
def profile():
    return Profile(
        weight_kg=80,
        waist_cm=86,
        wind_min_kn=12,
        wind_max_kn=25,
        skill="intermediate",
        style="twintip",
        spots=["Bat Galim", "Sdot Yam"],
        budget_ils=8000,
        condition_pref="used",
        travel_km=120,
        home_location="Haifa",
    )
