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
    "IE": ["ireland", "dublin", "cork"], "FR": ["france", "paris", "lyon"],
    "DE": ["germany", "berlin", "munich", "frankfurt", "hamburg", "cologne", "stuttgart"],
    "NL": ["netherlands", "amsterdam", "rotterdam", "utrecht"], "CH": ["switzerland", "zurich", "zürich", "geneva", "lausanne", "basel"],
    "ES": ["spain", "madrid", "barcelona"], "PT": ["portugal", "lisbon", "porto"], "PL": ["poland", "warsaw", "krakow", "kraków", "wroclaw"],
    "SE": ["sweden", "stockholm"], "IT": ["italy", "milan", "rome"], "RO": ["romania", "bucharest", "cluj"],
    "RS": ["serbia", "belgrade", "novi sad"], "CZ": ["czech republic", "czechia", "prague"], "AT": ["austria", "vienna"],
    "BE": ["belgium", "brussels"], "DK": ["denmark", "copenhagen"], "NO": ["norway", "oslo"], "FI": ["finland", "helsinki"],
    "GR": ["greece", "athens"], "HU": ["hungary", "budapest"], "UA": ["ukraine", "kyiv"], "EE": ["estonia", "tallinn"],
    "LT": ["lithuania", "vilnius"], "TR": ["turkey", "türkiye", "istanbul"], "SA": ["saudi arabia", "riyadh"], "QA": ["qatar", "doha"],
    "ZA": ["south africa", "cape town", "johannesburg"], "NG": ["nigeria", "lagos"], "KE": ["kenya", "nairobi"], "EG": ["egypt", "cairo"],
    "PK": ["pakistan", "karachi", "lahore"], "BD": ["bangladesh", "dhaka"], "LK": ["sri lanka", "colombo"],
    "CA": ["canada", "toronto", "vancouver", "montreal", "ontario", "british columbia", "quebec", "calgary", "ottawa", "waterloo"],
    "BR": ["brazil", "são paulo", "sao paulo"], "MX": ["mexico", "méxico", "guadalajara", "monterrey"],
    "AR": ["argentina", "buenos aires"], "CO": ["colombia", "bogota", "bogotá", "medellin", "medellín"], "CL": ["chile", "santiago"],
    "PE": ["peru", "lima"],
    "US": ["united states", "usa", "u.s.", "san francisco", "new york", "nyc", "seattle", "boston", "austin",
           "los angeles", "chicago", "palo alto", "mountain view", "menlo park", "sunnyvale", "san jose",
           "washington, dc", "denver", "atlanta", "miami", "bay area", "pittsburgh", "philadelphia", "san diego", "dallas",
           "houston", "salt lake city", "portland", "new mexico", "raleigh", "phoenix", "minneapolis", "detroit", "nashville",
           ", ca", ", ny", ", wa", ", tx", ", ma", ", co", ", il", ", ga", ", va", ", nc", ", pa", ", ut", ", fl", ", nj",
           ", dc", ", az", ", mn", ", oh", ", mi", ", md"],
}
REGIONS = {
    "Southeast Asia": {"SG", "MY", "ID", "TH", "VN", "PH"},
    "East Asia": {"HK", "TW", "JP", "KR", "CN"},
    "South Asia": {"IN", "PK", "BD", "LK"},
    "Oceania": {"AU", "NZ"},
    "Middle East": {"AE", "IL", "SA", "QA", "TR"},
    "Europe": {"GB", "IE", "FR", "DE", "NL", "CH", "ES", "PT", "PL", "SE", "IT", "RO", "RS", "CZ", "AT", "BE", "DK", "NO",
               "FI", "GR", "HU", "UA", "EE", "LT"},
    "North America": {"US", "CA"},
    "Latin America": {"BR", "MX", "AR", "CO", "CL", "PE"},
    "Africa": {"ZA", "NG", "KE", "EG"},
}
REMOTE = re.compile(r"\bremote\b|\banywhere\b|work from home|\bwfh\b", re.I)
_PATTERNS = [
    (code, name, re.compile(r"(?<![a-z])" + re.escape(name) + r"(?![a-z])", re.I))
    for code, names in COUNTRIES.items() for name in names
]
# Listed after each country's name but not cities: states, provinces, nations, areas, alternative spellings.
NOT_CITIES = {"new mexico", "british columbia", "quebec", "ontario", "bay area", "england", "scotland", "usa", "u.s.",
              "uae", "czechia", "türkiye", "méxico", "united arab emirates", "cambridge, uk"}
CITY_NAMES = {name for names in COUNTRIES.values() for name in names[1:]
              if not name.startswith(",") and name not in NOT_CITIES}


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
