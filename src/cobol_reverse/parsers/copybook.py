"""
Copybook parser — extracts a flat list of fields from a COBOL copybook.

Handles:
  - Fixed-form (sequence numbers cols 1-6, indicator col 7, content cols 8-72)
  - Free-form / indented-only files (no sequence numbers)
  - PIC / PICTURE clause with all common masks
  - OCCURS, REDEFINES, USAGE annotations
  - 88-level condition names (attached to their parent field)
  - Multi-line definitions (continuation lines)

Output: list of Field objects, ordered as they appear in the copybook.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
#  Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CopyField:
    level:       int
    name:        str
    pic:         str | None        # raw PIC string e.g. "9(5)V99"
    pic_type:    str | None        # "numeric" | "alphanumeric" | "alphanum-edited" | "numeric-edited"
    length:      int | None        # total byte length (best-effort)
    usage:       str | None        # COMP / COMP-3 / DISPLAY / BINARY …
    occurs:      int | None        # OCCURS n TIMES
    occurs_dep:  str | None        # OCCURS DEPENDING ON <field>
    redefines:   str | None        # REDEFINES <field>
    values_88:   list[str]         # 88-level values attached to this field
    source_line: int               # 1-based line number in the copybook
    is_group:    bool              # True if no PIC (group / record item)


# ─────────────────────────────────────────────────────────────────────────────
#  Patterns
# ─────────────────────────────────────────────────────────────────────────────

# A COBOL data line starts with a level number (01-49, 66, 77, 88)
_RE_LEVEL = re.compile(
    r"^\s*(\d{1,2})\s+([\w][\w-]*)(.*)", re.I
)

_RE_PIC = re.compile(
    r"\bPIC(?:TURE)?\s+(?:IS\s+)?([^\s,]+)", re.I
)
_RE_USAGE = re.compile(
    r"\bUSAGE\s+(?:IS\s+)?(COMP(?:-[1-5])?|BINARY|DISPLAY|INDEX|POINTER|PACKED-DECIMAL)",
    re.I
)
_RE_COMP_SHORT = re.compile(          # COMP without USAGE keyword
    r"\b(COMP(?:-[1-5])?|BINARY|PACKED-DECIMAL)\b", re.I
)
_RE_OCCURS = re.compile(
    r"\bOCCURS\s+(\d+)\s+(?:TIMES\s+)?", re.I
)
_RE_OCCURS_DEP = re.compile(
    r"\bOCCURS\s+\d+\s+TO\s+\d+\s+TIMES\s+DEPENDING\s+ON\s+([\w-]+)", re.I
)
_RE_REDEFINES = re.compile(
    r"\bREDEFINES\s+([\w-]+)", re.I
)
_RE_VALUE_88 = re.compile(
    r"\bVALUE\s+(?:IS\s+)?(.+?)(?:\.|$)", re.I
)


# ─────────────────────────────────────────────────────────────────────────────
#  PIC analysis helpers
# ─────────────────────────────────────────────────────────────────────────────

def _pic_type(pic: str) -> str:
    p = pic.upper()
    if re.search(r"[AX]", p):
        return "alphanumeric"
    if re.search(r"[9ZB+\-/,.]", p) and re.search(r"[B+\-/,.]", p):
        return "numeric-edited"
    if re.search(r"9", p):
        return "numeric"
    return "alphanumeric"


def _pic_length(pic: str) -> int | None:
    """Best-effort byte length from PIC string."""
    try:
        p = pic.upper().replace("(", " (")
        # Expand repetition: X(10) → XXXXXXXXXX
        expanded = re.sub(r"([A-Z9Z])\s*\((\d+)\)",
                          lambda m: m.group(1) * int(m.group(2)), p)
        # Remove V (virtual decimal point), S (sign), special chars
        chars = re.sub(r"[V]", "", expanded)
        return max(1, sum(1 for c in chars if c.isalpha() or c == "9"))
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  Content normaliser — same as inspect.py
# ─────────────────────────────────────────────────────────────────────────────

def _to_content(line: str) -> str:
    """Strip sequence number (cols 1-6) and indicator (col 7) if present."""
    if len(line) >= 7:
        indicator = line[6]
        if indicator in ("*", "/"):       # comment
            return ""
        return line[7:72]
    return line


def _is_comment(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("*") or (len(line) >= 7 and line[6] in ("*", "/"))


def _is_listing_header(line: str) -> bool:
    """
    Detect IBM/CA compiler listing page headers — lines that are NOT COBOL code.
    Typical patterns:
      - Lines starting with digits followed by spaces (sequence-only lines)
      - "PP 5655-..." IBM header lines
      - Lines with "PAGE " or "DATE " patterns typical of listings
      - Lines that are purely numeric (sequence numbers without code)
    """
    stripped = line.strip()
    if not stripped:
        return False
    # Pure page/sequence header: e.g. "   1  " with nothing else
    if re.match(r"^\s*\d+\s*$", line):
        return True
    # IBM/CA listing page header
    if re.match(r"^\s*(PP\s+\d|IEL|IGY|IDM|IGYCRP|DATE\s+\d)", stripped, re.I):
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
#  COBOL source / listing pre-processor
# ─────────────────────────────────────────────────────────────────────────────

# Division markers (order matters for extraction)
_RE_DATA_DIV = re.compile(r"\bDATA\s+DIVISION\b", re.I)
_RE_PROC_DIV = re.compile(r"\bPROCEDURE\s+DIVISION\b", re.I)
_RE_ANY_DIV  = re.compile(
    r"\b(IDENTIFICATION|ENVIRONMENT|DATA|PROCEDURE)\s+DIVISION\b", re.I
)

# Section markers inside DATA DIVISION
_RE_SECTION  = re.compile(
    r"\b(FILE|WORKING-STORAGE|LOCAL-STORAGE|LINKAGE|COMMUNICATION|REPORT)\s+SECTION\b",
    re.I,
)


def _normalise_line(raw: str) -> str:
    """
    Return the logical content of one raw line:
    - Strip listing headers → ""
    - Strip fixed-form cols 1-6 + indicator 7
    - Return free-form / already-stripped lines as-is
    """
    if _is_listing_header(raw):
        return ""
    return _to_content(raw)


def _extract_data_division(text: str) -> str:
    """
    If `text` looks like a full COBOL source or listing, extract only the
    DATA DIVISION block (stops at PROCEDURE DIVISION).
    If no DATA DIVISION marker is found, return the original text unchanged
    (assume it is already a copybook fragment).
    """
    lines = text.splitlines()
    start = None
    end   = len(lines)

    for i, raw in enumerate(lines):
        content = _normalise_line(raw)
        if start is None:
            if _RE_DATA_DIV.search(content):
                start = i       # include the DATA DIVISION header itself
        else:
            if _RE_PROC_DIV.search(content):
                end = i
                break

    if start is None:
        return text             # no DATA DIVISION found → treat as-is (pure copybook)

    return "\n".join(lines[start:end])


# ─────────────────────────────────────────────────────────────────────────────
#  Parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_copybook(text: str) -> list[CopyField]:
    """
    Parse a copybook or COBOL source and return an ordered list of CopyField objects.
    If the text contains a DATA DIVISION marker, only that section is parsed.
    Group items (no PIC) and 88-level conditions are included.
    """
    text      = _extract_data_division(text)   # no-op if already a pure copybook
    raw_lines = text.splitlines()

    # --- Pass 1: join continuation lines and normalise ---
    logical: list[tuple[int, str]] = []   # (1-based source line, content)
    buf_line = 0
    buf: list[str] = []

    for i, raw in enumerate(raw_lines, 1):
        if _is_comment(raw):
            continue
        content = _normalise_line(raw).rstrip()
        if not content.strip():
            continue

        # A new logical line starts if it begins with a level number
        if re.match(r"^\s*\d{1,2}\s", content):
            if buf:
                logical.append((buf_line, " ".join(buf)))
            buf = [content.strip()]
            buf_line = i
        else:
            # Continuation of previous logical line
            if buf:
                buf.append(content.strip())
            # else: free-form content before first level — skip

    if buf:
        logical.append((buf_line, " ".join(buf)))

    # --- Pass 2: parse each logical line ---
    fields: list[CopyField] = []
    pending_88: list[str] = []   # 88-level values to attach to previous field

    for src_line, text_line in logical:
        m = _RE_LEVEL.match(text_line)
        if not m:
            continue

        level = int(m.group(1))
        name  = m.group(2).upper().rstrip(".")
        rest  = m.group(3)

        # 88-level: attach values to the last real field
        if level == 88:
            mv = _RE_VALUE_88.search(rest)
            val = mv.group(1).strip() if mv else rest.strip()
            pending_88.append(val)
            continue

        # Flush pending 88-levels to the last real field
        if pending_88 and fields:
            fields[-1].values_88.extend(pending_88)
        pending_88 = []

        # Extract clauses
        pm = _RE_PIC.search(rest)
        pic = pm.group(1).upper() if pm else None

        um = _RE_USAGE.search(rest) or _RE_COMP_SHORT.search(rest)
        usage = um.group(1).upper() if um else None

        om = _RE_OCCURS.search(rest)
        occurs = int(om.group(1)) if om else None

        odm = _RE_OCCURS_DEP.search(rest)
        occurs_dep = odm.group(1).upper() if odm else None

        rdm = _RE_REDEFINES.search(rest)
        redefines = rdm.group(1).upper() if rdm else None

        pic_t = _pic_type(pic) if pic else None
        length = _pic_length(pic) if pic else None

        fields.append(CopyField(
            level       = level,
            name        = name,
            pic         = pic,
            pic_type    = pic_t,
            length      = length,
            usage       = usage,
            occurs      = occurs,
            occurs_dep  = occurs_dep,
            redefines   = redefines,
            values_88   = [],
            source_line = src_line,
            is_group    = (pic is None),
        ))

    # Flush final pending 88-levels
    if pending_88 and fields:
        fields[-1].values_88.extend(pending_88)

    return fields


def parse_greedy(text: str) -> list[CopyField]:
    """
    Greedy scanner for mainframe compiler listings and other noisy formats.

    Scans the ENTIRE text (not line-by-line) for all occurrences of:
        <level> <name> ... PIC <clause>
    and for group items:
        <level> <name>  (ends with dot or is followed by another level)

    Handles formats where:
    - Multiple source lines are concatenated on one physical line
    - Cross-reference data is interleaved with source code
    - Line numbers (6-digit) prefix source content

    Returns deduplicated CopyField list ordered by first occurrence.
    """
    # ── Step 1: extract source tokens from listing lines ──────────────────
    # Listing line pattern: 6-digit number (optionally followed by C/I) then content
    # e.g. "006113C  10  WS-DATE         PIC 9(8)."
    # Strip 6-digit prefixes so we get clean COBOL tokens.
    clean = re.sub(r"\b\d{6}[A-Z]?\s+", " ", text)
    # Also strip 8-digit object-code addresses  e.g. "00090300"
    clean = re.sub(r"\b[0-9A-F]{8}\b", " ", clean)
    # Collapse runs of spaces/stars/dashes used as separators
    clean = re.sub(r"[*\-]{3,}", " ", clean)

    # ── Step 2: find all PIC fields in the cleaned text ───────────────────
    # Pattern: level(1-2 digits) name PIC clause
    PAT_FIELD = re.compile(
        r"\b(\d{1,2})\s+([\w][\w-]*)\s+.*?PIC(?:TURE)?\s+(?:IS\s+)?([^\s.,]+)",
        re.I,
    )

    seen:   set[str] = set()
    fields: list[CopyField] = []

    for m in PAT_FIELD.finditer(clean):
        level = int(m.group(1))
        name  = m.group(2).upper().rstrip(".")
        pic   = m.group(3).upper()

        # Skip obvious noise: level 88, or names that are COBOL keywords
        _SKIP = {"COPY","MOVE","IF","THEN","ELSE","END","PERFORM","CALL",
                 "SECTION","DIVISION","PROCEDURE","PROGRAM","AUTHOR"}
        if level == 88 or name in _SKIP:
            continue
        if level < 1 or level > 77:
            continue

        key = f"{level}:{name}"
        if key in seen:
            continue
        seen.add(key)

        # Get surrounding text for USAGE / OCCURS
        ctx = clean[max(0, m.start()-10): m.end()+80]
        um  = _RE_USAGE.search(ctx) or _RE_COMP_SHORT.search(ctx)
        om  = _RE_OCCURS.search(ctx)

        fields.append(CopyField(
            level       = level,
            name        = name,
            pic         = pic,
            pic_type    = _pic_type(pic),
            length      = _pic_length(pic),
            usage       = um.group(1).upper() if um else None,
            occurs      = int(om.group(1))    if om else None,
            occurs_dep  = None,
            redefines   = None,
            values_88   = [],
            source_line = 0,   # not available in greedy mode
            is_group    = False,
        ))

    return fields


def parse_copybook_file(path: Path) -> list[CopyField]:
    """
    Read and parse a copybook or COBOL source/listing file.
    Strategy:
      1. Try standard copybook parser (handles fixed-form + free-form + full source).
      2. If fewer than 2 leaf fields found, fall back to greedy scan — handles
         mainframe compiler listings where source is interleaved with object code.
    """
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("latin-1", errors="replace")

    # Standard parse first
    fields = parse_copybook(text)
    leaves = [f for f in fields if not f.is_group]

    # Fallback: greedy scan for noisy listing formats
    if len(leaves) < 2:
        greedy = parse_greedy(text)
        if len(greedy) > len(leaves):
            return greedy

    return fields


def fields_summary(fields: list[CopyField]) -> str:
    """Human-readable summary of parsed fields."""
    leaf  = [f for f in fields if not f.is_group]
    group = [f for f in fields if f.is_group]
    lines = [
        f"Total entries : {len(fields)}",
        f"  Leaf fields : {len(leaf)}",
        f"  Group items : {len(group)}",
        f"  88-levels   : {sum(len(f.values_88) for f in fields)}",
    ]
    return "\n".join(lines)
