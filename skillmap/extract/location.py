"""Location free text → (country ISO-2, city, region, remote)."""

from __future__ import annotations

import re

COUNTRIES = {
    "SG": ["singapore"], "HK": ["hong kong"], "MY": ["malaysia", "kuala lumpur"], "ID": ["indonesia", "jakarta"],
    "TH": ["thailand", "bangkok"], "VN": ["vietnam", "ho chi minh", "hanoi"], "PH": ["philippines", "manila"],
    "TW": ["taiwan", "taipei"], "JP": ["japan", "tokyo", "osaka"], "KR": ["korea", "seoul"],
    "CN": ["china", "shanghai", "beijing", "shenzhen", "hangzhou"], "IN": ["india", "bangalore", "bengaluru",
    "hyderabad", "mumbai", "pune", "delhi", "gurgaon", "gurugram", "noida", "chennai"],
    "AU": ["australia", "sydney", "melbourne", "brisbane", "perth"], "NZ": ["new zealand", "auckland"],
    "AE": ["united arab emirates", "uae", "dubai", "abu dhabi"], "IL": ["israel", "tel aviv"],
    "GB": ["united kingdom", "london", "manchester", "edinburgh", "cambridge, uk", "england", "scotland"],
    "IE": ["ireland", "dublin"], "FR": ["france", "paris"], "DE": ["germany", "berlin", "munich", "frankfurt", "hamburg"],
    "NL": ["netherlands", "amsterdam"], "CH": ["switzerland", "zurich", "zürich", "geneva"], "ES": ["spain", "madrid", "barcelona"],
    "PT": ["portugal", "lisbon"], "PL": ["poland", "warsaw", "krakow"], "SE": ["sweden", "stockholm"],
    "CA": ["canada", "toronto", "vancouver", "montreal", "ontario"], "BR": ["brazil", "são paulo", "sao paulo"],
    "US": ["united states", "usa", "u.s.", "san francisco", "new york", "nyc", "seattle", "boston", "austin",
           "los angeles", "chicago", "palo alto", "mountain view", "menlo park", "sunnyvale", "san jose",
           "washington, dc", "denver", "atlanta", "miami", "bay area", ", ca", ", ny", ", wa", ", tx", ", ma"],
}
REGIONS = {
    "Southeast Asia": {"SG", "MY", "ID", "TH", "VN", "PH"},
    "East Asia": {"HK", "TW", "JP", "KR", "CN"},
    "South Asia": {"IN"},
    "Oceania": {"AU", "NZ"},
    "Middle East": {"AE", "IL"},
    "Europe": {"GB", "IE", "FR", "DE", "NL", "CH", "ES", "PT", "PL", "SE"},
    "North America": {"US", "CA"},
    "Latin America": {"BR"},
}
REMOTE = re.compile(r"\bremote\b|\banywhere\b|work from home|\bwfh\b", re.I)
_PATTERNS = [
    (code, name, re.compile(r"(?<![a-z])" + re.escape(name) + r"(?![a-z])", re.I))
    for code, names in COUNTRIES.items() for name in names
]
CITY_NAMES = {name for names in COUNTRIES.values() for name in names[1:] if not name.startswith(",")}


def region_of(country: str | None) -> str | None:
    if not country:
        return None
    return next((region for region, codes in REGIONS.items() if country in codes), "Other")


def normalise(location: str, country_hint: str | None = None, remote_hint: bool | None = None,
              ) -> tuple[str | None, str | None, str | None, bool]:
    text = location or ""
    country, city = (country_hint.upper() if country_hint else None), None
    best = None
    for code, name, pattern in _PATTERNS:
        match = pattern.search(text)
        if match and (best is None or match.start() < best[0]):
            best = (match.start(), code, name)
    if best:
        _, code, name = best
        country = country or code
        if name in CITY_NAMES and code == country:
            city = name.title()
    if country == "SG":
        city = "Singapore"
    if country == "HK":
        city = "Hong Kong"
    remote = bool(remote_hint) if remote_hint is not None else bool(REMOTE.search(text))
    return country, city, region_of(country), remote
