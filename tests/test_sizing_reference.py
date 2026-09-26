"""Family 1: recommended sizes must agree with published size charts.

The charts live in kitefinder/sizing/reference/*.yaml. Each chart cell is checked, so a change
to the sizing formula that drifts away from what the industry recommends fails loudly.
"""

import pytest

from kitefinder.sizing import engine

KITE_CHART = engine.load_reference("kite_chart_generic")
TWINTIP_CHART = engine.load_reference("twintip_chart_generic")
HARNESS_CHART = engine.load_reference("harness_chart_generic")

# Charts list sizes in whole square metres and adjacent bands overlap, so allow 1 m².
KITE_TOLERANCE_M2 = 1


def kite_cells():
    for row in KITE_CHART["rows"]:
        for (w_lo, w_hi), (s_lo, s_hi) in zip(
            KITE_CHART["wind_bands_kn"], row["sizes_m2"], strict=True
        ):
            yield pytest.param(
                row["weight_kg"], w_lo, w_hi, s_lo, s_hi, id=f"{row['weight_kg']}kg-{w_lo}kn"
            )


def test_kite_chart_is_well_formed():
    bands = KITE_CHART["wind_bands_kn"]
    assert all(lo < hi for lo, hi in bands)
    assert all(a[1] == b[0] for a, b in zip(bands, bands[1:], strict=False)), "bands contiguous"
    for row in KITE_CHART["rows"]:
        assert len(row["sizes_m2"]) == len(bands)
        lows = [lo for lo, _ in row["sizes_m2"]]
        assert lows == sorted(lows, reverse=True), "more wind -> smaller kite"


@pytest.mark.parametrize("weight, w_lo, w_hi, s_lo, s_hi", list(kite_cells()))
def test_kite_size_matches_chart(weight, w_lo, w_hi, s_lo, s_hi):
    # Charts give the size to rig at the bottom of each band.
    size = engine.kite_size_for(weight, w_lo)
    assert s_lo - KITE_TOLERANCE_M2 <= size <= s_hi + KITE_TOLERANCE_M2, (
        f"{weight} kg @ {w_lo} kn: engine says {size} m², chart says {s_lo}-{s_hi} m²"
    )


@pytest.mark.parametrize("weight, w_lo, w_hi, s_lo, s_hi", list(kite_cells()))
def test_chart_size_is_usable_in_its_band(weight, w_lo, w_hi, s_lo, s_hi):
    """The chart's kite should be flyable at the bottom of its band per our usable range."""
    lo, hi = engine.kite_wind_range((s_lo + s_hi) / 2, weight)
    assert lo <= w_lo + 1.5 and hi >= w_lo


@pytest.mark.parametrize("max_w, lo, hi", TWINTIP_CHART["rows"])
def test_twintip_length_matches_chart(max_w, lo, hi):
    weight = min(max_w, 120) - 1
    got_lo, got_hi, rec = engine.twintip_length(weight)
    assert (got_lo, got_hi) == (lo, hi)
    assert lo <= rec <= hi


def test_twintip_chart_monotonic():
    rows = TWINTIP_CHART["rows"]
    assert [r[0] for r in rows] == sorted(r[0] for r in rows)
    assert [r[1] for r in rows] == sorted(r[1] for r in rows)
    assert all(lo < hi for _, lo, hi in rows)


@pytest.mark.parametrize("label, lo, hi", HARNESS_CHART["sizes"])
def test_harness_mid_waist_gets_chart_size(label, lo, hi):
    assert engine.harness_sizes((lo + hi) / 2) == [label]


def test_harness_chart_has_no_gaps():
    sizes = HARNESS_CHART["sizes"]
    assert all(a[2] == b[1] for a, b in zip(sizes, sizes[1:], strict=False))


def test_reference_charts_declare_verification_state():
    """Charts must say whether they were checked against the live web (see the live suite)."""
    for chart in (KITE_CHART, TWINTIP_CHART, HARNESS_CHART):
        assert isinstance(chart["verified"], bool)
        assert isinstance(chart["sources"], list)
