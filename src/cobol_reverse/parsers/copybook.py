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
    raw = line[:8]
    stripped = line.strip()
    return stripped.startswith("*") or (len(line) >= 7 and line[6] in ("*", "/"))


# ─────────────────────────────────────────────────────────────────────────────
#  Parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_copybook(text: str) -> list[CopyField]:
    """
    Parse a copybook text and return an ordered list of CopyField objects.
    Group items (no PIC) and 88-level conditions are included.
    """
    raw_lines = text.splitlines()

    # --- Pass 1: join continuation lines and normalise ---
    logical: list[tuple[int, str]] = []   # (1-based source line, content)
    buf_line = 0
    buf: list[str] = []

    for i, raw in enumerate(raw_lines, 1):
        if _is_comment(raw):
            continue
        content = _to_content(raw).rstrip()
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


def parse_copybook_file(path: Path) -> list[CopyField]:
    """Read a copybook file and parse it."""
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("latin-1", errors="replace")
    return parse_copybook(text)


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
