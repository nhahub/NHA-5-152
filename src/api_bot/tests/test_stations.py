import pytest

from app.stations import ORDER, STATIONS, display_name, resolve_station


@pytest.mark.parametrize("text,expected", [
    ("helwan", "helwan"), ("Helwan", "helwan"), ("  HELWAN ", "helwan"), ("حلوان", "helwan"),
    ("new_cairo", "new_cairo"), ("New Cairo", "new_cairo"), ("new-cairo", "new_cairo"), ("القاهرة الجديدة", "new_cairo"), ("القاهره الجديده", "new_cairo"),
    ("october", "october"), ("6th of October", "october"), ("6 october", "october"), ("6 أكتوبر", "october"), ("اكتوبر", "october"), ("٦ أكتوبر", "october"),
    ("shobra", "shobra"), ("Shubra", "shobra"), ("شبرا", "shobra"),
    ("downtown", "downtown"), ("Down Town", "downtown"), ("وسط البلد", "downtown"),
])
def test_resolve_known(text, expected):
    assert resolve_station(text) == expected


@pytest.mark.parametrize("text", ["", "   ", None, "maadi", "cairo", "helwan2", "zamalek"])
def test_resolve_unknown(text):
    assert resolve_station(text) is None


def test_every_station_has_both_names_and_resolves_to_itself():
    assert ORDER == ["downtown", "helwan", "new_cairo", "october", "shobra"]
    for s in ORDER:
        assert STATIONS[s]["en"] and STATIONS[s]["ar"]
        assert resolve_station(s) == s and resolve_station(STATIONS[s]["en"]) == s and resolve_station(STATIONS[s]["ar"]) == s


def test_display_name():
    assert display_name("helwan", "en") == "Helwan"
    assert display_name("helwan", "ar") == "حلوان"
    assert display_name("unknown_x", "en") == "unknown_x"
