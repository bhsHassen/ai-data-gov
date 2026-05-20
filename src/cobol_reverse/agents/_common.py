"""
Shared helpers for the migration agents.
- Listing cleanup (strip object code + page headers)
- DATA DIVISION / PROCEDURE DIVISION block extraction
- Token-budget truncation with line numbers
"""
from __future__ import annotations

import re

_RE_OBJ_HEX  = re.compile(r"^\s*[0-9A-F]{6,8}\s+[0-9A-F]{2,}\s")
_RE_PAGE_HDR = re.compile(r"^\s*(PP\s+\d|IEL|IGY|IDM|IGYCRP|LineID|PAGE\s+\d)", re.I)
_RE_DATE_HDR = re.compile(r"^\s*\d{2}[/.-]\d{2}[/.-]\d{2,4}\s+\d{2}[:.]\d{2}")
_RE_FORMFEED = re.compile(r"[\f\x0c]")


def clean_listing(text: str) -> str:
    """Remove object code, page headers, timestamp banners."""
    out: list[str] = []
    for raw in text.splitlines():
        line = _RE_FORMFEED.sub("", raw)
        if not line.strip():
            out.append(line)
            continue
        if _RE_OBJ_HEX.match(line) or _RE_PAGE_HDR.match(line) or _RE_DATE_HDR.match(line):
            continue
        out.append(line)
    return "\n".join(out)


def numbered(text: str, max_chars: int | None = None) -> str:
    """Return text with line numbers in the left margin (` 0123  source line`)."""
    lines = text.splitlines()
    out   = "\n".join(f"{i:>6}  {ln}" for i, ln in enumerate(lines, 1))
    if max_chars and len(out) > max_chars:
        out = out[:max_chars] + f"\n[... truncated — {len(lines)} lines total ...]"
    return out


def extract_block(source: str, start_kw: str, end_kw: str | None = None,
                  max_chars: int = 60_000) -> str:
    """
    Return the slice of `source` between the first occurrence of `start_kw`
    (uppercase, e.g. 'PROCEDURE DIVISION') and `end_kw` (or end of file).
    Empty string if `start_kw` not found.
    """
    lines = source.splitlines()
    start = None
    end   = len(lines)
    for i, ln in enumerate(lines):
        u = ln.upper()
        if start is None and start_kw in u:
            start = i
        elif start is not None and end_kw and end_kw in u:
            end = i
            break
    if start is None:
        return ""
    block = "\n".join(lines[start:end])
    if len(block) > max_chars:
        block = block[:max_chars] + f"\n[... truncated — {start_kw} block ...]"
    return block


def head(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n[... truncated — {len(text)} chars total ...]"


# ─────────────────────────────────────────────────────────────────────────────
#  Shared anti-hallucination preamble — kept short, every agent appends its
#  own specific rules on top.
# ─────────────────────────────────────────────────────────────────────────────

ANTI_HALLU = """\
RÈGLES STRICTES ANTI-HALLUCINATION
- Chaque affirmation doit être directement vérifiable dans le code/spec fourni.
- Quand tu cites un comportement, donne le numéro de ligne entre crochets : [ligne 245].
- Si une information demandée n'est pas trouvable dans le matériau fourni,
  écris explicitement « (information non disponible dans le code fourni) ».
- N'INVENTE JAMAIS de noms de paragraphes, de tables, de variables ou de
  numéros de ligne. Préfère « non détecté » à une supposition.
- Réponds en français. Les noms COBOL restent en MAJUSCULES.
"""
