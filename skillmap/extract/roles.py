"""Role family from the title. Order matters: the first family whose rule matches wins."""

from __future__ import annotations

import re

FAMILIES = ["fde", "ai_ml", "data", "backend", "frontend", "infra", "quant", "trading_biz", "other_eng"]
LABELS = {
    "fde": "Forward deployed / solutions",
    "ai_ml": "AI/ML engineering",
    "data": "Data engineering & science",
    "backend": "Backend / Python",
    "frontend": "Frontend & mobile",
    "infra": "Infra, DevOps & security",
    "quant": "Quant & trading tech",
    "trading_biz": "Trading desk, ops & product",
    "other_eng": "Other engineering",
}

RULES: list[tuple[str, re.Pattern[str]]] = [
    ("fde", re.compile(r"forward[- ]deployed|\bfde\b|solutions? (?:engineer|architect)|customer (?:engineer|success engineer)|"
                       r"deployment (?:engineer|strategist)|field engineer|implementation engineer|sales engineer|"
                       r"pre-?sales|technical account|partner engineer", re.I)),
    ("ai_ml", re.compile(r"\bai\b|\bml\b|machine learning|\bllm|gen ?ai|generative|deep learning|computer vision|\bnlp\b|"
                         r"applied scientist|research (?:engineer|scientist)|mlops|prompt engineer|agentic|agents? engineer|"
                         r"artificial intelligence|data scientist", re.I)),
    # "quant" alone also matched quantum computing, and "trading" product managers and ops desks.
    ("quant", re.compile(r"\bquants?\b|\bquantitative\b|\balgo(?:rithmic)?[- ]?trad|\bhft\b|high[- ]frequency|market[- ]mak|"
                         r"(?:trading|execution|\balgo\b).*\b(?:engineer|developer|programmer|sre|systems?|software|technolog)|"
                         r"\b(?:engineer|developer|programmer|software|technolog\w*)\b.*\btrading", re.I)),
    # Trading jobs without a technical title (traders, trading ops and support, trading product
    # managers): not engineering, but who markets firms hire, so counted in their own group.
    ("trading_biz", re.compile(r"\btrad(?:ing|er|ers)\b|sales (?:and|&) trading|market[- ]making|\bexecution trader", re.I)),
    ("data", re.compile(r"data (?:engineer|analyst|platform|architect|infrastructure)|analytics engineer|\betl\b|"
                        r"business intelligence|\bbi (?:engineer|developer)|data scien", re.I)),
    ("infra", re.compile(r"devops|\bsre\b|site reliability|platform engineer|infrastructure|cloud (?:engineer|architect)|"
                         r"security|devsecops|network engineer|systems engineer|reliability", re.I)),
    ("frontend", re.compile(r"front[- ]?end|\bui\b|\bios\b|android|mobile|react|web developer", re.I)),
    ("backend", re.compile(r"back[- ]?end|python|software (?:engineer|developer)|\bswe\b|full[- ]?stack|"
                           r"(?:golang|go|java|node|rust|c\+\+) (?:engineer|developer)|api engineer|developer|software architect|"
                           r"programmer|founding engineer|member of technical staff", re.I)),
    ("other_eng", re.compile(r"engineer|scientist|technical lead|tech lead|cto\b", re.I)),
]


# Engineering outside software: these titles say "engineer" or "scientist" but their ads are about
# buildings, plants, vehicles or labs, and would dilute the software skill shares.
NOT_SOFTWARE = re.compile(
    r"\b(?:facilities|facility|mechanical|civil|structural|chemical|process engineer|manufacturing|commissioning|"
    r"electrical|hvac|plumbing|construction|maintenance|mechatronics|aerospace|vehicle|automotive|piping|"
    r"clinical|laboratory|lab scientist|biomedical|environmental|geotechnical|mining|petroleum|marine|"
    r"field service|service engineer|quality engineer|test technician|technician|sales engineer \(|"
    r"(?:vacuum|pumps?|valves?|compressors?|instrumentation|semiconductor equipment)\b)", re.I)
# Titles that name software work win over the list above ("Software Engineer, Vehicle Platform").
SOFTWARE = re.compile(r"software|\bml\b|machine learning|\bai\b|data|cloud|backend|frontend|full[- ]?stack|"
                      r"firmware|embedded|devops|platform|\bsre\b|security|solutions?|forward[- ]deployed|"
                      r"\bapi\b|developer|programmer", re.I)


# Non-English titles, rewritten into the English words the rules above look for. Order matters:
# whole phrases first, then single words; then "engineer de data" is turned round.
_WORDS: list[tuple[re.Pattern[str], str]] = [(re.compile(a, re.I), b) for a, b in [
    (r"l[ií]der t[ée]cnic[oa]|lideran[cç]a t[ée]cnica|chef de projet technique|technische[rn]? (?:leiter|lead)", "tech lead"),
    (r"aprendizaje autom[aá]tico|aprendizado de m[aá]quina|apprentissage automatique|maschinelle[snm]? lernen|"
     r"apprendimento automatico|machinaal leren", "machine learning"),
    (r"inteligencia artificial|intelig[eê]ncia artificial|intelligence artificielle|k[üu]nstliche[rn]? intelligenz|"
     r"intelligenza artificiale|kunstmatige intelligentie|(?-i:\bIA\b|\bKI\b)", "AI"),
    # German compounds: Softwareentwickler, Java-Entwicklerin, Dateningenieur, Backend-Entwickler.
    (r"(\w*?)-?entwickler(?:in(?:nen)?)?\b", r"\1 developer"),
    (r"(\w*?)-?ingenieur(?:in)?\b", r"\1 engineer"),
    (r"(\w*?)-?architekt(?:in)?\b", r"\1 architect"),
    (r"\b(?:desarrollador|desenvolvedor|d[ée]veloppeu(?:r|se)|sviluppatore|ontwikkelaar|programador|programmeur|"
     r"programmierer)(?:a|es|as|s|in)?\b", "developer"),
    (r"\b(?:ingenier[oa]|engenheir[oa]|ing[ée]nieure?|ingegnere)s?\b", "engineer"),
    (r"\b(?:cient[ií]fic[oa]|cientista|scientifique|wissenschaftler(?:in)?|scienziato)s?\b", "scientist"),
    (r"\b(?:arquitect[oa]|arquitet[oa]|architecte|architetto)s?\b", "architect"),
    (r"\b(?:analista|analyste)s?\b", "analyst"),
    (r"\bsoftwareontwikkelaar\b", "software developer"),
    (r"\b(?:datos|dados|donn[ée]es|daten|dati|gegevens)\b", "data"),
    (r"\b(?:seguridad|seguran[cç]a|s[ée]curit[ée]|sicherheit|sicurezza|beveiliging)\b", "security"),
    (r"\b(?:nube|nuvem|nuage)\b", "cloud"),
    (r"\b(?:soluciones|solu[cç][oõ]es|solutions|l[öo]sungs?)\b", "solutions"),
    (r"\b(?:infraestructura|infraestrutura|infrastruktur|infrastruttura)\b", "infrastructure"),
    (r"\b(?:plataforma|plateforme|plattform|piattaforma)\b", "platform"),
    (r"\b(?:mec[aá]nic[oa]|m[ée]canique|mec[aâ]nic[oa]|maschinenbau\w*)\b", "mechanical"),
    (r"\b(?:el[ée]ctric[oa]|el[ée]trico|[ée]lectrique|elektro\w*|elettrico)\b", "electrical"),
    (r"\b(?:qu[ií]mic[oa]|chimique|chemie\w*)\b", "chemical"),
    (r"\b(?:mantenimiento|manuten[cç][aã]o|instandhaltung|manutenzione)\b", "maintenance"),
    (r"\b(?:t[ée]cnic[oa]|technicien(?:ne)?|techniker(?:in)?)\b", "technician"),
    (r"\b(?:calidad|qualidade|qualit[ée]|qualit[äa]t|qualit[àa])\b", "quality"),
    (r"\b(?:procesos|processos|proc[ée]d[ée]s|prozess\w*)\b", "process"),
    (r"\b(?:construcci[oó]n|constru[cç][aã]o|bau\w*)\b", "construction"),
    # Japanese and Korean katakana / hangul job words.
    (r"エンジニア|엔지니어", " engineer "), (r"データ|데이터", " data "), (r"機械学習|머신러닝", " machine learning "),
    (r"ソフトウェア|소프트웨어", " software "), (r"バックエンド|백엔드", " backend "), (r"フロントエンド|프론트엔드", " frontend "),
    (r"インフラ|인프라", " infrastructure "), (r"セキュリティ|보안", " security "), (r"開発者|개발자|プログラマー", " developer "),
    (r"サイエンティスト|과학자", " scientist "), (r"アーキテクト|아키텍트", " architect "),
]]
_PREPOSITIONS = re.compile(r"\b(?:de|des|du|da|do|em|en|di|del|della|der|in|of)\s+(?=(?:data|software|machine learning|AI|cloud|"
                           r"security|infrastructure|platform|backend|frontend|solutions)\b)", re.I)
_TURN = re.compile(r"\b(engineer|developer|scientist|analyst|architect)\s+(data|software|machine learning|AI|cloud|security|"
                   r"infrastructure|platform|backend|back-end|frontend|front-end|full[- ]?stack|mechanical|electrical|"
                   r"chemical|quality|process|maintenance|construction|solutions)\b", re.I)


def english(title: str) -> str:
    """The title with common non-English job words in English ("Ingeniero de Datos" → "data engineer")."""
    if title.isascii() and not re.search(r"entwickler|ingenie|desarroll|desenvolv|veloppeu|programad|programmeu|"
                                         r"cient|scientif|wissenschaft|arquite|architect[eo]|analist|analyste|ontwikkel|"
                                         r"datos|dados|donn|daten|dati\b|seguri|securit|sicher|nube|nuvem|plataf|plattf|"
                                         r"infraestr|infrastruk|soluci|solu[cç]|aprendiz|apprentiss|maschinell|intelig|intellig|k[üu]nstl|"
                                         r"(?-i:\bIA\b|\bKI\b)|ingegner|sviluppat|programmier|beveilig|gegevens|"
                                         r"kunstmatig|machinaal|t[ée]cnic|mec[aâ]n|el[ée]ctr|qu[ií]m|calidad|qualid|lider",
                                         title, re.I):
        return title
    out = title
    for pattern, replacement in _WORDS:
        out = pattern.sub(replacement, out)
    if out == title:  # nothing was translated: leave an English title's word order alone
        return title
    out = _PREPOSITIONS.sub("", out)
    out = _TURN.sub(r"\2 \1", out)
    return re.sub(r"\s+", " ", out).strip()


def classify(title: str) -> str | None:
    """None means not a technical role; those postings are kept but left out of skill stats."""
    title = english(title or "")
    if NOT_SOFTWARE.search(title or "") and not SOFTWARE.search(title or ""):
        return None
    for family, rule in RULES:
        if rule.search(title or ""):
            return family
    return None
