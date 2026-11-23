"""The 5 districts (station ids used in forecasts.csv), their display names and the spellings users may type."""
from __future__ import annotations

import re

STATIONS: dict[str, dict] = {
    "downtown": {"en": "Downtown", "ar": "وسط البلد",
                 "aliases": ["downtown", "down town", "وسط البلد", "وسط", "downtown cairo"]},
    "helwan": {"en": "Helwan", "ar": "حلوان", "aliases": ["helwan", "حلوان"]},
    "new_cairo": {"en": "New Cairo", "ar": "القاهرة الجديدة",
                  "aliases": ["new cairo", "newcairo", "new_cairo", "القاهرة الجديدة", "القاهره الجديده", "التجمع", "التجمع الخامس"]},
    "october": {"en": "6th of October", "ar": "6 أكتوبر",
                "aliases": ["october", "6th of october", "6 october", "6th october", "6 of october", "6th of oct",
                            "6 أكتوبر", "6 اكتوبر", "أكتوبر", "اكتوبر", "السادس من أكتوبر", "٦ أكتوبر"]},
    "shobra": {"en": "Shobra", "ar": "شبرا", "aliases": ["shobra", "shubra", "شبرا"]},
}
ORDER = list(STATIONS)

_AR_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي", "ؤ": "و", "ئ": "ي", "٦": "6"})


def _norm(text: str) -> str:
    """Lower-case, drop Arabic diacritics/tatweel, unify Arabic letter variants, remove spaces and punctuation."""
    text = re.sub(r"[\u064B-\u0652\u0640]", "", str(text)).lower().translate(_AR_MAP)
    return re.sub(r"[\s_\-.,،]+", "", text)


_LOOKUP = {_norm(a): sid for sid, info in STATIONS.items() for a in [sid, info["en"], info["ar"], *info["aliases"]]}


def resolve_station(text: str | None) -> str | None:
    """'New Cairo', 'new_cairo', 'القاهرة الجديدة', '6th of October' ... -> station id, or None."""
    if not text or not text.strip():
        return None
    return _LOOKUP.get(_norm(text))


def display_name(station: str, lang: str = "en") -> str:
    info = STATIONS.get(station)
    return info[lang] if info else station
