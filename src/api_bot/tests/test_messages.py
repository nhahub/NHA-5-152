import re
from datetime import date

from app.forecast_store import CATEGORY_RANK, Forecast
from bot.messages import ADVICE, CATEGORY_LABEL, HORIZON_LABEL, LANGS, LEVEL_LABEL, TEXT, advice_for, build_alert_message, fmt_date, forecast_line, t

PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _f(h=1, pm=60.0, prob=0.7, level="high", cat="Unhealthy", station="helwan"):
    d = date(2026, 11, 22)
    return Forecast(station, d, date(2026, 11, 22 + h), h, pm, prob, level, cat)


def test_both_languages_have_the_same_keys_and_placeholders():
    assert set(TEXT["en"]) == set(TEXT["ar"])
    for k in TEXT["en"]:
        assert set(PLACEHOLDER.findall(TEXT["en"][k])) == set(PLACEHOLDER.findall(TEXT["ar"][k])), k


def test_label_tables_are_complete():
    for lang in LANGS:
        assert set(LEVEL_LABEL[lang]) == {"none", "warning", "high"}
        assert set(CATEGORY_LABEL[lang]) >= set(CATEGORY_RANK)
        assert set(HORIZON_LABEL[lang]) == {1, 2, 3}
        assert "_default" in ADVICE[lang]
        assert all(ADVICE[lang].get(c) for c in ("Unhealthy for Sensitive Groups", "Unhealthy", "Very Unhealthy", "Hazardous"))


def test_arabic_texts_are_arabic_and_english_are_not():
    arabic = re.compile(r"[\u0600-\u06FF]")
    for k in ("welcome", "help", "alert_title", "footer", "choose_district", "subscribed"):
        assert arabic.search(TEXT["ar"][k]), k
    assert not arabic.search(TEXT["en"]["alert_title"]) and not arabic.search(TEXT["en"]["footer"])


def test_dates():
    assert fmt_date("en", date(2026, 11, 23)) == "23 Nov" and fmt_date("ar", date(2026, 11, 23)) == "23 نوفمبر"


def test_forecast_line_contents():
    en = forecast_line("en", _f(1, 62.4, 0.9))
    assert "Tomorrow" in en and "23 Nov" in en and "62" in en and "90%" in en and "High risk" in en and "(Unhealthy)" in en
    ar = forecast_line("ar", _f(2, 47.0, 0.3, "none", "Unhealthy for Sensitive Groups"))
    assert "بعد يومين" in ar and "غير صحي للفئات الحساسة" in ar and "لا يوجد تنبيه" in ar


def test_alert_message_has_title_blocks_advice_and_honest_footer():
    m = build_alert_message("en", date(2026, 11, 22), {"helwan": [_f(1), _f(2, 56, 0.6, "warning", "Unhealthy")]})
    assert m.startswith("⚠️ <b>Air-quality alert</b>") and "<b>Helwan</b>" in m and "Everyone should reduce" in m
    assert "miss many high-pollution days" in m and "/unsubscribe all" in m
    assert "Updated forecast" not in m and "Updated forecast" in build_alert_message("en", date(2026, 11, 22), {"helwan": [_f(1)]}, updated=True)


def test_advice_uses_worst_category_and_default_for_mild():
    assert "stay indoors" in advice_for("en", [_f(cat="Unhealthy"), _f(cat="Very Unhealthy")]).lower()
    assert ADVICE["en"]["_default"] in advice_for("en", [_f(cat="Moderate", level="warning")])


def test_messages_stay_below_telegram_limit():
    blocks = {s: [_f(h, station=s) for h in (1, 2, 3)] for s in ("downtown", "helwan", "new_cairo", "october", "shobra")}
    for lang in LANGS:
        assert len(build_alert_message(lang, date(2026, 11, 22), blocks)) < 3500


def test_t_falls_back_to_arabic_for_unknown_language():
    assert t("xx", "ack_done") == TEXT["ar"]["ack_done"]
