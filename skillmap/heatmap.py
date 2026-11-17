"""The skills heatmap as a standalone SVG, for a GitHub profile README.

Same data as the website's heatmap: one row per in-demand skill, one column per target role
family (cell = share of postings asking for it), and a final column with the owner's own rating.
Colours follow the viewer's light/dark preference via a media query inside the SVG.
"""

from __future__ import annotations

from html import escape

ROW_H = 22
LABEL_W = 250
CELL_W = 92
HEADER_H = 64
FOOTER_H = 34
LEVELS = {0: "—", 1: "Aware", 2: "Used", 3: "Comfortable", 4: "Strong", 5: "Expert"}
SHORT_FAMILY = {"ai_ml": "AI / ML", "fde": "Forward dep.", "backend": "Backend / Py", "data": "Data"}


def render(me: dict, as_of: str) -> str:
    families = me["families"]
    rows = me["rows"]
    width = LABEL_W + CELL_W * (len(families) + 1) + 16
    height = HEADER_H + ROW_H * len(rows) + FOOTER_H
    peak = max((share for row in rows for share in row["demand"].values()), default=0) or 1
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-labelledby="t d">',
        "<title id=\"t\">Skills heatmap: market demand vs my level</title>",
        f'<desc id="d">Share of job postings asking for each skill, by role family, next to my self-rated level. '
        f"Data as of {escape(as_of)}.</desc>",
        "<style>"
        ":root{--bg:#F4F3EF;--ink:#14161B;--soft:#5C6068;--rule:#DDDAD2;--demand:31,79,216;--me:46,107,79}"
        "@media (prefers-color-scheme:dark){:root{--bg:#15171C;--ink:#E7E5DF;--soft:#A0A4AC;--rule:#2E323A;"
        "--demand:126,162,255;--me:111,193,152}}"
        "text{font:12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;fill:var(--ink)}"
        ".soft{fill:var(--soft)}.h{font-weight:600}.n{font-size:10px}.hi{fill:#fff}"
        "@media (prefers-color-scheme:dark){.hi{fill:#14161B}}"
        "</style>",
        f'<rect width="{width}" height="{height}" rx="8" style="fill:var(--bg)"/>',
        '<text x="12" y="22" class="h">Skills demand vs my level</text>',
        f'<text x="12" y="38" class="soft n">% of tech job postings asking for each skill · as of {escape(as_of)}</text>',
    ]
    for index, family in enumerate([*families, {"label": "Me", "n": None, "key": "me"}]):
        x = LABEL_W + index * CELL_W + CELL_W / 2
        label = SHORT_FAMILY.get(family["key"], family["label"])
        out.append(f'<text x="{x}" y="{HEADER_H - 10}" text-anchor="middle" class="h n">{escape(label)}</text>')
    for r, row in enumerate(rows):
        y = HEADER_H + r * ROW_H
        name = row["name"]
        label = name if len(name) <= 31 else name[:30].rstrip(" /(") + "…"
        out.append(f'<text x="12" y="{y + 15}"><title>{escape(name)}</title>{escape(label)}</text>')
        for c, family in enumerate(families):
            share = row["demand"].get(family["key"], 0) or 0
            opacity = round(0.08 + 0.92 * (share / peak), 3) if share else 0.03
            x = LABEL_W + c * CELL_W
            out.append(f'<rect x="{x + 2}" y="{y + 2}" width="{CELL_W - 4}" height="{ROW_H - 4}" rx="3" '
                       f'style="fill:rgba(var(--demand),{opacity})"/>')
            ink = " hi" if share / peak > 0.55 else ""
            out.append(f'<text x="{x + CELL_W / 2}" y="{y + 15}" text-anchor="middle" class="n{ink}">'
                       f"{round(share * 100)}%</text>")
        rating = row.get("rating")
        x = LABEL_W + len(families) * CELL_W
        opacity = round(0.15 + 0.17 * rating, 3) if rating else 0.03
        out.append(f'<rect x="{x + 2}" y="{y + 2}" width="{CELL_W - 4}" height="{ROW_H - 4}" rx="3" '
                   f'style="fill:rgba(var(--me),{opacity})"/>')
        ink = " hi" if rating and rating >= 4 else ""
        out.append(f'<text x="{x + CELL_W / 2}" y="{y + 15}" text-anchor="middle" class="n{ink}">'
                   f"{escape(LEVELS.get(rating or 0, '—'))}</text>")
    y = height - 12
    out.append(f'<text x="12" y="{y}" class="soft n">pierreblachere.com/skills · method and sources on the page</text>')
    out.append("</svg>")
    return "\n".join(out)
