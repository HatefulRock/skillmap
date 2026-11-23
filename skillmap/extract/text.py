"""HTML → plain text, and splitting text into requirement vs nice-to-have sections."""

from __future__ import annotations

import html
import re

_BLOCK_TAGS = re.compile(r"<\s*(?:br|/p|/li|/h\d|/div|/tr|li|p|h\d)[^>]*>", re.I)
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t ]+")

NICE_HEADING = re.compile(
    r"^\W*(?:nice[- ]to[- ]haves?|bonus(?: points)?|preferred(?: qualifications| skills)?|good to have|"
    r"pluses|plus points|it(?:'s| is| would be) (?:a )?(?:plus|bonus|great if)|desirable|"
    r"what would make you stand out|extra credit|optional)\b",
    re.I,
)
SECTION_HEADING = re.compile(
    r"^\W*(?:requirements?|qualifications?|what you(?:'ll)? (?:need|bring|have)|you(?:'ll)? have|"
    r"must[- ]haves?|minimum qualifications|basic qualifications|about (?:you|the role|us)|responsibilities|"
    r"what you(?:'ll)? do|the role|benefits|perks|why join|who you are|skills)\b",
    re.I,
)
NICE_INLINE = re.compile(r"\b(?:is a (?:big )?plus|a plus\b|nice to have|preferred\b|bonus\b|advantageous|desirable)", re.I)


# Chinese, Japanese and Korean count as word characters, so "熟悉Python和" has no word boundary
# around Python; a space at each script change lets \b patterns match inside CJK text.
_CJK = "\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af"
_CJK_EDGE = re.compile(rf"(?<=[{_CJK}])(?=[A-Za-z0-9])|(?<=[A-Za-z0-9+#])(?=[{_CJK}])")


def to_plain(text: str) -> str:
    text = html.unescape(text or "")
    text = _BLOCK_TAGS.sub("\n", text)
    text = _TAGS.sub(" ", text)
    text = html.unescape(text)  # double-escaped sources
    text = _CJK_EDGE.sub(" ", text)
    lines = [_SPACES.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def split_sections(plain: str) -> tuple[list[str], list[str]]:
    """Returns (core lines, nice-to-have lines).

    A line is nice-to-have if it sits under a "Nice to have"/"Bonus"/"Preferred" heading, or says
    so itself ("Experience with Rust is a plus"). Everything else is core.
    """
    core: list[str] = []
    nice: list[str] = []
    in_nice = False
    for line in plain.splitlines():
        short = len(line) < 60
        if short and NICE_HEADING.match(line):
            in_nice = True
            remainder = NICE_HEADING.sub("", line).strip(" :-")
            if remainder:
                nice.append(remainder)
            continue
        if short and SECTION_HEADING.match(line):
            in_nice = False
        if in_nice or NICE_INLINE.search(line):
            nice.append(line)
        else:
            core.append(line)
    return core, nice
