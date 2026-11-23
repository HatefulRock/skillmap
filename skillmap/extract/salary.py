"""Advertised pay read from posting text, and a common unit to compare it in.

Many ads state pay in the text even when the source has no salary field: US and EU pay-
transparency laws put a range in most Greenhouse and Lever ads, and HN posts often give one.
`from_text` finds the first plausible range; `annual` converts any stored figure to a yearly
amount; `to_usd` uses a fixed rate table so numbers compare across countries.

Only stated ranges count. "Competitive salary" and equity-only figures are ignored, and a
figure that makes no sense for its period (a $40 "annual" salary) is dropped rather than guessed.
"""

from __future__ import annotations

import re

# Approximate USD value of one unit, November 2026. Pay comparisons are about tens of percent,
# so a yearly refresh of this table is enough.
USD_PER_UNIT = {
    "USD": 1.0, "SGD": 0.76, "GBP": 1.30, "EUR": 1.13, "CAD": 0.72, "AUD": 0.66, "HKD": 0.128, "CHF": 1.22,
    "INR": 0.0118, "JPY": 0.0066, "CNY": 0.14, "KRW": 0.00072, "AED": 0.272, "PLN": 0.27, "SEK": 0.10,
    "NZD": 0.60, "MYR": 0.23, "IDR": 0.000062, "PHP": 0.017, "THB": 0.029, "VND": 0.000039, "TWD": 0.031,
    "BRL": 0.18, "MXN": 0.055, "ILS": 0.27, "DKK": 0.15, "NOK": 0.094, "CZK": 0.048,
    "CLP": 0.00105, "COP": 0.00025, "ARS": 0.00085, "PEN": 0.27, "UYU": 0.024, "ZAR": 0.055, "TRY": 0.025,
    "RON": 0.23, "HUF": 0.0028, "BGN": 0.58, "UAH": 0.024, "PKR": 0.0036, "BDT": 0.0082, "NGN": 0.00065,
    "EGP": 0.02, "KES": 0.0077, "LKR": 0.0034,
}
COUNTRY_CURRENCY = {
    "US": "USD", "SG": "SGD", "GB": "GBP", "CA": "CAD", "AU": "AUD", "HK": "HKD", "CH": "CHF", "IN": "INR",
    "JP": "JPY", "CN": "CNY", "KR": "KRW", "AE": "AED", "PL": "PLN", "SE": "SEK", "NZ": "NZD", "MY": "MYR",
    "ID": "IDR", "PH": "PHP", "TH": "THB", "VN": "VND", "TW": "TWD", "BR": "BRL", "MX": "MXN", "IL": "ILS",
    "DK": "DKK", "NO": "NOK", "CZ": "CZK", "CL": "CLP", "CO": "COP", "AR": "ARS", "PE": "PEN", "UY": "UYU",
    "ZA": "ZAR", "TR": "TRY", "RO": "RON", "HU": "HUF", "BG": "BGN", "UA": "UAH", "PK": "PKR", "BD": "BDT",
    "NG": "NGN", "EG": "EGP", "KE": "KES", "LK": "LKR",
    **{c: "EUR" for c in ["DE", "FR", "NL", "IE", "ES", "IT", "PT", "BE", "AT", "FI", "EE", "LT", "LV", "LU", "GR"]},
}
DOLLAR_SIGN = {"USD", "SGD", "CAD", "AUD", "HKD", "NZD", "MXN", "CLP", "COP", "ARS", "UYU"}
PERIODS_PER_YEAR = {"year": 1, "month": 12, "week": 52, "day": 260, "hour": 2080}

_SYMBOL = {"S$": "SGD", "SG$": "SGD", "HK$": "HKD", "A$": "AUD", "AU$": "AUD", "C$": "CAD", "CA$": "CAD",
           "US$": "USD", "NZ$": "NZD", "£": "GBP", "€": "EUR", "₹": "INR", "¥": "JPY", "₩": "KRW", "R$": "BRL"}
_CODES = "|".join(sorted(USD_PER_UNIT, key=len, reverse=True))
_SYMBOLS = "|".join(re.escape(s) for s in sorted(_SYMBOL, key=len, reverse=True))
_CUR = rf"(?:(?P<{{n}}sym>{_SYMBOLS}|\$)|(?P<{{n}}code>{_CODES})\b\s?\$?)"
_NUM = r"(?P<{n}>\d{{1,3}}(?:[,.\s]\d{{3}})+(?:[.,]\d{{2}}(?!\d))?|\d+(?:\.\d+)?)\s?(?P<{n}k>[kK]\b|[mM]\b)?"

_RANGE = re.compile(
    _CUR.format(n="c1") + r"?\s?" + _NUM.format(n="lo") + r"\s?(?P<c1post>" + _CODES + r"|€|£)?"
    r"\s*(?:-|–|—|to|and)\s*"
    + _CUR.format(n="c2") + r"?\s?" + _NUM.format(n="hi") + r"(?:\s?(?P<c2post>(?:" + _CODES + r")\b|€|£))?",
    re.I,
)
_PERIOD = re.compile(
    r"(?P<hour>per hour|an hour|/\s?h(?:ou)?r\b|hourly)|(?P<day>per day|a day|/\s?day\b|daily rate)|"
    r"(?P<week>per week|a week|/\s?w(?:ee)?k\b|weekly)|"
    r"(?P<month>per month|a month|/\s?mo(?:nth)?\b|monthly|p\.?m\.?\b|mensual(?:es)?|al mes|por mes|monatlich|pro monat|par mois|mensuel|月[薪给給収]|/月)|"
    r"(?P<year>per year|per annum|a year|/\s?y(?:ea)?r\b|annual(?:ly|ised|ized)?|yearly|p\.?a\.?\b|base salary|OTE|anual(?:es)?|al año|por año|j[äa]hrlich|pro jahr|par an\b|annuel|年[薪収]|/年)",
    re.I,
)
# Words that make a nearby number something other than pay.
_NOT_PAY = re.compile(r"\b(?:raised|funding|series [a-e]|valuation|revenue|arr\b|customers|users|employees|"
                      r"downloads|transactions|aum|assets under|million users|bonus of|sign-?on)\b", re.I)
_PAY_CONTEXT = re.compile(r"\b(?:salary|salaries|pay|compensation|base|ote|range|annual|per annum|per month|"
                          r"per hour|hourly|monthly|wage|remuneration|package|usd|sgd|gbp|eur)\b", re.I)

# Plausible yearly pay in USD; anything outside is a misread (a year, a headcount, a funding round).
_MIN_USD_YEAR, _MAX_USD_YEAR = 4_000, 1_500_000


def _number(text: str, suffix: str | None) -> float:
    text = re.sub(r"(?<=\d{3})[.,]\d{2}$", "", text.strip())  # cents: "212,700.00", "45.000,00"
    digits = re.sub(r"[,\s]", "", text)
    # "1.200.000" (European thousands) vs "120.5" (decimal): dots followed by exactly 3 digits are separators.
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", digits):
        digits = digits.replace(".", "")
    value = float(digits)
    if suffix and suffix.lower() == "k":
        value *= 1_000
    elif suffix and suffix.lower() == "m":
        value *= 1_000_000
    return value


def _currency(match: re.Match[str], country: str | None) -> str | None:
    for side in ("c1", "c2"):
        code = match.group(f"{side}code") or match.group(f"{side}post")
        if code:
            return _SYMBOL.get(code, code.upper())
        symbol = match.group(f"{side}sym")
        if symbol == "¥" and country == "CN":
            return "CNY"  # the same sign for yuan and yen
        if symbol and symbol != "$":
            return _SYMBOL.get(symbol.upper(), _SYMBOL.get(symbol))
        if symbol == "$":
            # "$" is also the peso sign across Latin America.
            return COUNTRY_CURRENCY.get(country or "", "USD") if COUNTRY_CURRENCY.get(country or "") in DOLLAR_SIGN \
                else "USD"
    return None


def _period(window: str, low: float, currency: str) -> str | None:
    found = _PERIOD.search(window)
    if found:
        return found.lastgroup
    # No stated period: infer from size in USD terms.
    usd = low * USD_PER_UNIT.get(currency, 1.0)
    if usd >= 15_000:
        return "year"
    if 1_000 <= usd < 15_000:
        return "month"
    return None


def from_text(text: str, country: str | None = None) -> tuple[float, float, str, str] | None:
    """(min, max, currency, period) for the first stated pay range, or None."""
    for match in _RANGE.finditer(text or ""):
        currency = _currency(match, country)
        if not currency:
            continue  # bare numbers ("5-10 years") are never pay
        start, end = match.start(), match.end()
        before, after = text[max(0, start - 120):start], text[end:end + 60]
        if _NOT_PAY.search(before[-60:] + after[:30]):
            continue
        # A bare "$" range needs a pay word nearby; "£80k", "SGD 6,000" or "$150k" say enough themselves.
        explicit = currency != "USD" or match.group("c1code") or match.group("c2code") or match.group("lok") \
            or match.group("hik")
        if not explicit and not _PAY_CONTEXT.search(before + after):
            continue
        low = _number(match.group("lo"), match.group("lok") or match.group("hik"))
        high = _number(match.group("hi"), match.group("hik"))
        if high < low:
            continue
        period = _period(after + " " + before[-40:], low, currency)
        if not period:
            continue
        yearly_usd = low * PERIODS_PER_YEAR[period] * USD_PER_UNIT.get(currency, 1.0)
        if not _MIN_USD_YEAR <= yearly_usd <= _MAX_USD_YEAR or high > low * 4:
            continue
        return low, high, currency, period
    return None


# A figure above this for its period is a yearly amount filed under the wrong period
# (a "monthly" 180,000 SGD, a "weekly" 290,000 USD).
_MAX_USD_PER = {"month": 40_000, "week": 10_000, "day": 2_500, "hour": 400}


def clean(low: float | None, high: float | None, currency: str | None, period: str | None
          ) -> tuple[float, float, str, str] | None:
    """A source's pay fields made comparable, or None when they can't be trusted.

    Fixes the slips boards make: yearly pay given in thousands ("140-245"), yearly pay filed as
    monthly or weekly, and monthly pay filed as yearly. Anything still implausible is dropped."""
    if not low or not currency or period not in PERIODS_PER_YEAR or currency not in USD_PER_UNIT:
        return None
    low, high = float(low), float(high or low)
    if high < low:
        low, high = high, low
    rate = USD_PER_UNIT[currency]
    if period == "year" and high * rate < 1_000:
        low, high = low * 1_000, high * 1_000  # "140-245" meaning 140k-245k
    elif period == "year" and low * rate < 1_000 <= high * rate and low * 1_000 <= high * 1.5:
        low *= 1_000  # "195 - 415,000"
    if period != "year" and low * rate > _MAX_USD_PER[period] and _MIN_USD_YEAR <= low * rate <= _MAX_USD_YEAR:
        period = "year"
    elif period == "year" and low * rate < 8_000 and _MIN_USD_YEAR <= low * 12 * rate <= _MAX_USD_YEAR:
        period = "month"
    yearly = low * PERIODS_PER_YEAR[period] * rate
    if not _MIN_USD_YEAR <= yearly <= _MAX_USD_YEAR or high > low * 4:
        return None
    return low, high, currency, period


def annual(value: float | None, period: str | None) -> float | None:
    if value is None or period not in PERIODS_PER_YEAR:
        return None
    return float(value) * PERIODS_PER_YEAR[period]


def to_usd(value: float | None, currency: str | None) -> float | None:
    if value is None or currency not in USD_PER_UNIT:
        return None
    return float(value) * USD_PER_UNIT[currency]
