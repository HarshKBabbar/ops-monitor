"""NOTAM text parsing.

Two input styles are supported:
  * ICAO format  - "(A1234/26 NOTAMN  Q) ...  A) VIDP B) 2610061200 C) 2610101800 E) ...)"
                   used by email briefings, JetPlan exports and manual paste.
  * AAI summary  - the monthly "NOTAM SUMMARY" PDFs published on aim-india.aai.aero
                   (airport header line, then "A2087/26 2607031250 / 2610021324" + text).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

# AAI summary file name -> FIR ICAO code (used to keep NOTAM numbers unique per NOF)
FIR_BY_NAME = {"CHENNAI": "VOMF", "DELHI": "VIDF", "MUMBAI": "VABF", "KOLKATA": "VECF"}


@dataclass
class Notam:
    series: str
    number: int
    year: int
    fir: str = ""
    notam_type: str = "N"          # N = new, R = replace, C = cancel
    ref_id: str = ""               # NOTAM replaced / cancelled (for R and C)
    location: str = ""             # item A
    airport_name: str = ""
    valid_from: str | None = None  # ISO UTC
    valid_to: str | None = None    # ISO UTC, None for PERM
    valid_to_text: str = ""        # "PERM" / "EST" / ""
    schedule: str = ""             # item D
    text: str = ""                 # item E
    lower: str = ""
    upper: str = ""
    q_code: str = ""
    raw: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def notam_id(self) -> str:
        return f"{self.series}{self.number:04d}/{self.year % 100:02d}"

    @property
    def key(self) -> str:
        return f"{self.fir or '----'}|{self.notam_id}"


# ---------------------------------------------------------------- helpers
def parse_time(value: str) -> str | None:
    """YYMMDDhhmm (UTC) -> ISO string."""
    value = (value or "").strip()
    if not re.fullmatch(r"\d{10}", value):
        return None
    try:
        dt = datetime.strptime(value, "%y%m%d%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return dt.isoformat()


def clean(text: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln).strip()


TAG_RULES = [
    ("AD CLOSED", re.compile(r"\b(AIRFIELD|AERODROME|AD|AIRPORT)\b (CLOSURE|CLSD|CLOSED)\b")),
    ("RWY CLOSED", re.compile(r"\bRWY\b.*\b(CLSD|CLOSED|NOT AVBL|NOT AVAILABLE)\b|\b(CLSD|CLOSED)\b.*\bRWY\b", re.S)),
    ("DECLARED DIST", re.compile(r"\b(TORA|TODA|ASDA|LDA)\b")),
    ("THRESHOLD", re.compile(r"\b(DISPLACED|DTHR|THR\b.*\bDISPLACED)")),
    ("OBSTACLE", re.compile(r"\b(OBST|OBSTACLE|OBSTACLES|CRANE|CRANES)\b")),
    ("NAVAID", re.compile(r"\b(ILS|LOC|LLZ|GP|GLIDE ?PATH|LOCALI[SZ]ER|DME|VOR|DVOR|NDB|TACAN|MLS|GBAS|NAVAIDS?|NAV AIDS?)\b")),
    ("WIP", re.compile(r"\b(WIP|WORK IN PROGRESS|WORK IN PROG|CONST|CONSTRUCTION)\b")),
    ("RWY", re.compile(r"\bRWY\b")),
]
# Always performance-critical on their own
CRITICAL_TAGS = {"AD CLOSED", "DECLARED DIST", "THRESHOLD", "OBSTACLE", "NAVAID"}
# Critical only together (RWY closed alone is not; RWY closed due WIP is)
CRITICAL_COMBO = {"RWY CLOSED", "WIP"}


def critical_tags(tags: list[str]) -> list[str]:
    """Tags that make a NOTAM performance-critical (empty = not critical)."""
    s = set(tags)
    out = [t for t in tags if t in CRITICAL_TAGS]
    if CRITICAL_COMBO <= s:
        out += [t for t in tags if t in CRITICAL_COMBO]
    return out


# Navaid names used only as a position reference / area name, not about the navaid itself
NAVAID_IGNORE = re.compile(
    r"FLT ID/NDB"                                                    # met balloon transmitter id
    r"|\bVO[RDP] ?\d+"                                               # Indian restricted / danger area e.g. VOR 186(A)
    r"|\d+(\.\d+)? ?(NM|KM|MILES)\s+(FM|FROM|TO)\s+'?\w+'?\s+'?(D?VOR|NDB|DME)\b"  # 09NM FM JJP VOR
    r"|\bTO\s+'\w+ (D?VOR|NDB)'"                                      # 40 MILES TO 'DPN VOR'
)


def compute_tags(schedule: str, text: str, q_code: str = "") -> list[str]:
    body = f"{schedule}\n{text}".upper()
    nav_body = NAVAID_IGNORE.sub(" ", body)
    tags = [name for name, rx in TAG_RULES if rx.search(nav_body if name == "NAVAID" else body)]
    if "NAVAID" not in tags and q_code[:2] in ("QI", "QN"):     # Q-code ILS / radio navaid
        tags.append("NAVAID")
    if "RWY CLOSED" in tags and "RWY" in tags:
        tags.remove("RWY")
    return tags


def tag(n: Notam) -> Notam:
    n.tags = compute_tags(n.schedule, n.text, n.q_code)
    return n


# ---------------------------------------------------------------- ICAO format
ICAO_START = re.compile(
    r"\(?\b([A-Z])(\d{1,4})/(\d{2})\s+NOTAM([NRC])(?:\s+([A-Z])(\d{1,4})/(\d{2}))?"
)
ITEM_SPLIT = re.compile(r"(?:^|\s)([QABCDEFG])\)\s*")


def parse_icao(text: str) -> list[Notam]:
    text = text.replace("\r", "")
    starts = list(ICAO_START.finditer(text))
    out: list[Notam] = []
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        chunk = text[m.start():end].strip()
        body = text[m.end():end]
        items: dict[str, str] = {}
        parts = ITEM_SPLIT.split(body)
        for j in range(1, len(parts) - 1, 2):
            items.setdefault(parts[j], parts[j + 1].strip())
        if "A" not in items and "E" not in items:
            continue
        year = 2000 + int(m.group(3))
        n = Notam(series=m.group(1), number=int(m.group(2)), year=year, notam_type=m.group(4))
        if m.group(5):
            n.ref_id = f"{m.group(5)}{int(m.group(6)):04d}/{m.group(7)}"
        q = items.get("Q", "")
        qparts = [p.strip() for p in q.split("/")]
        if qparts and re.fullmatch(r"[A-Z]{4}", qparts[0] or ""):
            n.fir = qparts[0]
        if len(qparts) > 1:
            n.q_code = qparts[1]
        n.location = (items.get("A", "").split() or [""])[0]
        n.valid_from = parse_time(items.get("B", "")[:10])
        c = items.get("C", "").upper()
        if "PERM" in c:
            n.valid_to_text = "PERM"
        else:
            n.valid_to = parse_time(c[:10])
            if "EST" in c:
                n.valid_to_text = "EST"
        n.schedule = clean(items.get("D", ""))
        e = items.get("E", "")
        n.text = clean(e.rstrip(")").rstrip())
        n.lower = clean(items.get("F", "")).rstrip(")")
        n.upper = clean(items.get("G", "")).rstrip(")")
        n.raw = chunk
        out.append(tag(n))
    return out


# ---------------------------------------------------------------- AAI summary format
AAI_ENTRY = re.compile(
    r"^\s*([A-Z])(\d{4})/(\d{2})\s+(\d{10})\s*/\s*(\d{10}|PERM)\s*(EST)?\s*(.*)$"
)
AAI_HEADER = re.compile(r"^\s*([A-Z][A-Z .,'()&/-]{2,}?)\s+(V[A-Z]{3})\s*$")
AAI_NOISE = [
    re.compile(r"NOTAM SUMMARY", re.I),
    re.compile(r"\bPage\s+\d+(\s+of\s+\d+)?\s*$"),          # "Page 5 of 16" / "Page     5 of 16" / "Page 5"
]
NOT_ICAO = {"VIEW", "VERY", "VIDE", "VISA", "VOID", "VIZ", "VALID"}
FIR_NAME_LINE =re.compile(r"^\s*(CHENNAI|DELHI|MUMBAI|KOLKATA)\s*$", re.I)
SCHEDULE_RX = re.compile(
    r"^(MON|TUE|WED|THU|FRI|SAT|SUN|DAILY|H24|SR|SS|HJ|HN|\d{4}-\d{4}|\d{2} ?(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC))"
)


@dataclass
class AaiSummary:
    fir: str
    series: str
    checklist: dict[int, set[int]]   # year -> numbers valid on summary date
    notams: list[Notam]


def parse_aai_summary(text: str, fir: str = "", series: str = "") -> AaiSummary:
    # drop page headers/footers (and the lone FIR-name line some offices print above them)
    lines: list[str] = []
    for ln in text.replace("\r", "").splitlines():
        if any(rx.search(ln) for rx in AAI_NOISE):
            if lines and FIR_NAME_LINE.match(lines[-1]):
                lines.pop()
            continue
        lines.append(ln)

    # checklist: "YEAR=2026 0935 1577 ..." blocks (wrapped over several lines) before the first NOTAM
    checklist: dict[int, set[int]] = {}
    joined = "\n".join(lines)
    first_entry = re.search(r"^\s*[A-Z]\d{4}/\d{2}\s+\d{10}", joined, re.M)
    head = joined[: first_entry.start()] if first_entry else joined
    start_line = 0
    for ym in re.finditer(r"YEAR\s*=\s*(\d{4})([\d\s]*)", head):
        checklist[int(ym.group(1))] = {int(x) for x in ym.group(2).split()}
        start_line = joined[: ym.end()].count("\n")

    notams: list[Notam] = []
    airport, icao, name_extra = "", "", []
    cur: Notam | None = None
    body: list[str] = []

    def flush():
        nonlocal cur, body
        if cur is None:
            return
        first = body[0].strip() if body else ""
        if first and SCHEDULE_RX.match(first) and len(first) < 70:
            cur.schedule = first
            body = body[1:]
        cur.text = clean("\n".join(body))
        cur.raw = f"{cur.notam_id} {cur.location}\n{cur.schedule}\n{cur.text}".strip()
        notams.append(tag(cur))
        cur, body = None, []

    for ln in lines[start_line:]:
        m = AAI_ENTRY.match(ln)
        if m:
            flush()
            cur = Notam(series=m.group(1), number=int(m.group(2)), year=2000 + int(m.group(3)), fir=fir)
            cur.location = icao
            cur.airport_name = ", ".join([airport] + name_extra) if airport else ""
            cur.valid_from = parse_time(m.group(4))
            if m.group(5) == "PERM":
                cur.valid_to_text = "PERM"
            else:
                cur.valid_to = parse_time(m.group(5))
                if m.group(6):
                    cur.valid_to_text = "EST"
            if m.group(7).strip():
                body.append(m.group(7))
            continue
        h = AAI_HEADER.match(ln)
        if (h and not re.search(r"[\d.,:]", ln) and len(ln.split()) <= 6
                and h.group(2) not in NOT_ICAO):
            flush()
            airport, icao, name_extra = h.group(1).strip(), h.group(2), []
            continue
        if cur is None:
            # lines between airport header and first NOTAM -> city name
            if airport and ln.strip() and len(name_extra) < 2:
                name_extra.append(ln.strip())
            continue
        body.append(ln)
    flush()
    return AaiSummary(fir=fir, series=series, checklist=checklist, notams=notams)


def parse_any(text: str, default_fir: str = "") -> list[Notam]:
    """Best effort for free text (email body, pasted text, exported file)."""
    found = parse_icao(text)
    if not found:
        found = parse_aai_summary(text, fir=default_fir).notams
    for n in found:
        if not n.fir and default_fir:
            n.fir = default_fir
    return found
