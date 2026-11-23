"""All user-facing text, in Arabic (ar) and English (en). Dynamic values are HTML-escaped by the callers."""
from __future__ import annotations

from datetime import date
from html import escape

from app.forecast_store import CATEGORY_RANK, Forecast, LEVEL_RANK
from app.stations import display_name

LANGS = ("ar", "en")

MONTHS = {
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "ar": ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"],
}
LEVEL_LABEL = {
    "en": {"none": "No alert", "warning": "⚠️ Warning", "high": "🔴 High risk"},
    "ar": {"none": "لا يوجد تنبيه", "warning": "⚠️ تحذير", "high": "🔴 خطر مرتفع"},
}
CATEGORY_LABEL = {
    "en": {c: c for c in CATEGORY_RANK},
    "ar": {"Good": "جيد", "Moderate": "متوسط", "Unhealthy for Sensitive Groups": "غير صحي للفئات الحساسة",
           "Unhealthy": "غير صحي", "Very Unhealthy": "غير صحي جداً", "Hazardous": "خطر"},
}
HORIZON_LABEL = {
    "en": {1: "Tomorrow", 2: "In 2 days", 3: "In 3 days"},
    "ar": {1: "غداً", 2: "بعد يومين", 3: "بعد 3 أيام"},
}
ADVICE = {
    "en": {
        "Unhealthy for Sensitive Groups": "Children, older adults and people with asthma or heart/lung disease should limit long or heavy outdoor activity.",
        "Unhealthy": "Everyone should reduce long or heavy outdoor activity; sensitive groups should stay indoors if possible.",
        "Very Unhealthy": "Avoid outdoor activity; sensitive groups should stay indoors.",
        "Hazardous": "Stay indoors and keep physical activity light.",
        "_default": "People with asthma or heart/lung disease may want to take it easy outdoors.",
    },
    "ar": {
        "Unhealthy for Sensitive Groups": "على الأطفال وكبار السن ومرضى الربو والقلب والرئة تقليل الأنشطة الخارجية الطويلة أو المجهدة.",
        "Unhealthy": "يُنصح الجميع بتقليل الأنشطة الخارجية الطويلة أو المجهدة، ويفضّل أن تبقى الفئات الحساسة في الأماكن المغلقة.",
        "Very Unhealthy": "تجنّب الأنشطة الخارجية، وعلى الفئات الحساسة البقاء في الأماكن المغلقة.",
        "Hazardous": "ابقَ في مكان مغلق وقلّل المجهود البدني.",
        "_default": "قد يفضّل مرضى الربو والقلب والرئة تخفيف المجهود في الخارج.",
    },
}

TEXT = {
    "en": {
        "welcome": ("👋 <b>Cairo air-quality alerts</b>\n"
                    "I message you when PM2.5 is forecast to reach the level you choose, for the next 1-3 days, in the Cairo districts.\n\n"
                    "<b>Start:</b> /subscribe\n<b>Forecast now:</b> /forecast helwan\n<b>My alerts:</b> /status\n"
                    "<b>Stop:</b> /unsubscribe all\n<b>Language / اللغة:</b> /lang ar · /lang en\n\nAll commands: /help"),
        "help": ("<b>Commands</b>\n"
                 "/subscribe - choose a district and alert level (buttons)\n"
                 "/subscribe helwan high - subscribe directly (level: warning or high; district: name or <code>all</code>)\n"
                 "/unsubscribe helwan - stop alerts for a district (<code>all</code> = everything)\n"
                 "/status - your subscriptions and their latest forecast\n"
                 "/forecast helwan - forecast for a district\n"
                 "/districts - the districts\n"
                 "/lang ar - العربية · /lang en - English\n\n"
                 "<b>Levels:</b> ⚠️ warning = pollution is likely to reach unhealthy-for-sensitive-groups levels; "
                 "🔴 high = health-risk day is very likely. <i>warning</i> gives you more alerts (and more false alarms)."),
        "districts": "<b>Districts</b>\n{items}\nExample: /subscribe {example} warning",
        "choose_district": "Which district do you want alerts for?",
        "choose_level": "Alert level for <b>{district}</b>?\n⚠️ <b>Warning</b> - earlier and more alerts (more false alarms)\n🔴 <b>High</b> - only when a risk day is very likely",
        "btn_all": "All districts",
        "subscribed": "✅ Subscribed: <b>{district}</b> - {level}. You will get a message when it applies.",
        "subscribed_all": "✅ Subscribed to <b>all districts</b> - {level}.",
        "updated": "🔄 Updated: <b>{district}</b> - {level}.",
        "unsub_done": "✅ Removed: {district}.",
        "unsub_all_done": "✅ All your subscriptions were removed.",
        "unsub_none": "You are not subscribed to {district}.",
        "unsub_empty": "You have no subscriptions. Use /subscribe.",
        "unsub_usage": "Which one? /unsubscribe helwan, or /unsubscribe all.\nYour subscriptions:\n{items}",
        "unknown_district": "I do not know the district “{text}”. Choose one of:\n{items}",
        "bad_level": "The level must be <b>warning</b> or <b>high</b>.",
        "status_header": "<b>Your alerts</b>",
        "status_empty": "You have no subscriptions yet. Use /subscribe.",
        "status_line": "• <b>{district}</b> - {level}",
        "forecast_header": "📍 <b>{district}</b> - forecast issued {date}",
        "forecast_line": "• {when} ({date}): {level} · PM2.5 ≈ {pm} µg/m³ ({category}) · risk {risk}%",
        "forecast_usage": "Which district? Example: /forecast helwan\n{items}",
        "stale": "⚠️ The latest forecast is {age} day(s) old, so it may not be current.",
        "no_data": "Forecasts are not available right now. Please try again later.",
        "lang_set": "Language set to English.",
        "lang_usage": "Use /lang ar or /lang en.",
        "unknown_command": "I do not know that command. Send /help.",
        "text_hint": "Use the commands: /subscribe to get alerts, /forecast to see a forecast, /help for everything.",
        "alert_title": "⚠️ <b>Air-quality alert</b> (forecast issued {date})",
        "alert_updated": "🔄 <i>Updated forecast</i>",
        "alert_line": "• {when} ({date}): {level} - PM2.5 ≈ {pm} µg/m³ ({category}), risk {risk}%",
        "advice": "💡 {text}",
        "footer": "ℹ️ Forecasts are estimates. At 48-72 hours the alerts miss many high-pollution days.\nChange: /status · Stop: /unsubscribe all",
        "blocked_note": "",
        "ack_done": "Done",
        "ack_error": "Something went wrong",
    },
    "ar": {
        "welcome": ("👋 <b>تنبيهات جودة الهواء في القاهرة</b>\n"
                    "أرسل لك رسالة عندما يُتوقع أن يصل تركيز PM2.5 إلى المستوى الذي تختاره خلال 1-3 أيام في أحياء القاهرة.\n\n"
                    "<b>للبدء:</b> /subscribe\n<b>التوقعات الآن:</b> /forecast helwan\n<b>اشتراكاتي:</b> /status\n"
                    "<b>للإيقاف:</b> /unsubscribe all\n<b>Language / اللغة:</b> /lang ar · /lang en\n\nكل الأوامر: /help"),
        "help": ("<b>الأوامر</b>\n"
                 "/subscribe - اختر الحي ومستوى التنبيه (أزرار)\n"
                 "/subscribe helwan high - اشتراك مباشر (المستوى: warning أو high، والحي: اسمه أو <code>all</code>)\n"
                 "/unsubscribe helwan - إيقاف تنبيهات حي (<code>all</code> = الكل)\n"
                 "/status - اشتراكاتك وآخر توقع لها\n"
                 "/forecast helwan - توقعات حي\n"
                 "/districts - الأحياء\n"
                 "/lang ar - العربية · /lang en - English\n\n"
                 "<b>المستويات:</b> ⚠️ تحذير = من المتوقع أن يصل التلوث لمستوى غير صحي للفئات الحساسة؛ "
                 "🔴 خطر مرتفع = احتمال كبير ليوم خطر على الصحة. مستوى <i>warning</i> يعطيك تنبيهات أكثر (وإنذارات خاطئة أكثر)."),
        "districts": "<b>الأحياء</b>\n{items}\nمثال: /subscribe {example} warning",
        "choose_district": "لأي حي تريد التنبيهات؟",
        "choose_level": "مستوى التنبيه لحي <b>{district}</b>؟\n⚠️ <b>تحذير</b> - تنبيهات أبكر وأكثر (إنذارات خاطئة أكثر)\n🔴 <b>خطر مرتفع</b> - فقط عندما يكون يوم الخطر شبه مؤكد",
        "btn_all": "كل الأحياء",
        "subscribed": "✅ تم الاشتراك: <b>{district}</b> - {level}. سيصلك تنبيه عند تحقق الشرط.",
        "subscribed_all": "✅ تم الاشتراك في <b>كل الأحياء</b> - {level}.",
        "updated": "🔄 تم التحديث: <b>{district}</b> - {level}.",
        "unsub_done": "✅ تم الإلغاء: {district}.",
        "unsub_all_done": "✅ تم إلغاء كل اشتراكاتك.",
        "unsub_none": "أنت غير مشترك في {district}.",
        "unsub_empty": "ليس لديك اشتراكات. استخدم /subscribe.",
        "unsub_usage": "أي حي؟ /unsubscribe helwan أو /unsubscribe all.\nاشتراكاتك:\n{items}",
        "unknown_district": "لا أعرف الحي «{text}». اختر من:\n{items}",
        "bad_level": "المستوى يجب أن يكون <b>warning</b> أو <b>high</b>.",
        "status_header": "<b>اشتراكاتك</b>",
        "status_empty": "ليس لديك اشتراكات بعد. استخدم /subscribe.",
        "status_line": "• <b>{district}</b> - {level}",
        "forecast_header": "📍 <b>{district}</b> - توقعات صادرة بتاريخ {date}",
        "forecast_line": "• {when} ({date}): {level} · PM2.5 ≈ {pm} µg/m³ ({category}) · احتمال الخطر {risk}%",
        "forecast_usage": "أي حي؟ مثال: /forecast helwan\n{items}",
        "stale": "⚠️ آخر توقع عمره {age} يوم، فقد لا يكون حديثاً.",
        "no_data": "التوقعات غير متاحة حالياً. حاول لاحقاً.",
        "lang_set": "تم اختيار اللغة العربية.",
        "lang_usage": "استخدم /lang ar أو /lang en.",
        "unknown_command": "لا أعرف هذا الأمر. أرسل /help.",
        "text_hint": "استخدم الأوامر: /subscribe للاشتراك في التنبيهات، /forecast لعرض التوقعات، /help لكل الأوامر.",
        "alert_title": "⚠️ <b>تنبيه جودة الهواء</b> (توقعات صادرة بتاريخ {date})",
        "alert_updated": "🔄 <i>توقعات محدّثة</i>",
        "alert_line": "• {when} ({date}): {level} - PM2.5 ≈ {pm} µg/m³ ({category})، احتمال الخطر {risk}%",
        "advice": "💡 {text}",
        "footer": "ℹ️ التوقعات تقديرية. عند 48-72 ساعة تفوّت التنبيهات كثيراً من أيام التلوث المرتفع.\nللتعديل: /status · للإيقاف: /unsubscribe all",
        "blocked_note": "",
        "ack_done": "تم",
        "ack_error": "حدث خطأ",
    },
}


def t(lang: str, key: str, **kw) -> str:
    lang = lang if lang in LANGS else "ar"
    return TEXT[lang][key].format(**kw)


def fmt_date(lang: str, d: date) -> str:
    return f"{d.day} {MONTHS[lang if lang in LANGS else 'ar'][d.month - 1]}"


def level_label(lang: str, level: str) -> str:
    return LEVEL_LABEL[lang][level]


def forecast_line(lang: str, f: Forecast, key: str = "forecast_line") -> str:
    return t(lang, key, when=HORIZON_LABEL[lang][f.horizon], date=fmt_date(lang, f.target_date), level=level_label(lang, f.alert_level),
             pm=f"{f.pred_pm25:.0f}", category=CATEGORY_LABEL[lang].get(f.aqi_category, escape(f.aqi_category)), risk=f"{100 * f.risk_prob:.0f}")


def advice_for(lang: str, rows: list[Forecast]) -> str:
    """One advice sentence for the worst category among the rows (and 'high' level counts as at least 'Unhealthy for Sensitive Groups')."""
    worst = max((r.aqi_category for r in rows), key=lambda c: CATEGORY_RANK.get(c, 0), default="")
    text = ADVICE[lang].get(worst) if CATEGORY_RANK.get(worst, 0) >= CATEGORY_RANK["Unhealthy for Sensitive Groups"] else ADVICE[lang]["_default"]
    return t(lang, "advice", text=text or ADVICE[lang]["_default"])


def build_alert_message(lang: str, issue_date: date, blocks: dict[str, list[Forecast]], updated: bool = False) -> str:
    """blocks: district -> the forecast rows that triggered an alert for this user."""
    parts = [t(lang, "alert_title", date=fmt_date(lang, issue_date))]
    if updated:
        parts.append(t(lang, "alert_updated"))
    all_rows: list[Forecast] = []
    for station, rows in blocks.items():
        parts.append("")
        parts.append(f"📍 <b>{escape(display_name(station, lang))}</b>")
        parts.extend(forecast_line(lang, r, "alert_line") for r in rows)
        all_rows.extend(rows)
    parts.append("")
    parts.append(advice_for(lang, all_rows))
    parts.append(t(lang, "footer"))
    return "\n".join(parts)
