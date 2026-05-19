"""
File inspector — given a directory of unknown text files, classify each one:

    * COBOL module    -> has IDENTIFICATION DIVISION / PROCEDURE DIVISION
    * Copybook        -> data definition only (01/05 levels, PIC, no DIVISIONs)
    * JCL             -> "//STEP EXEC PGM=..." statements
    * Unknown         -> couldn't decide, needs human eyes

For each file we also surface:
    - line / byte count
    - encoding detected
    - column-7 indicator usage (clue for fixed-form COBOL)
    - first signal line (the line that triggered the classification)
    - quick fingerprint: PROGRAM-ID, top-level copybook record name, etc.
    - flags: EXEC SQL (DB2), EXEC CICS, COPY statements, CALL statements

The output is a JSON report (machine-readable) AND a printed summary table.
No LLM is used — pure regex + heuristics. This must be fast and deterministic.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable

from .console import log


# --------------------------------------------------------------------------- #
#  Patterns                                                                     #
# --------------------------------------------------------------------------- #

# Fixed-form COBOL: cols 1-6 sequence numbers, col 7 indicator, 8-72 content.
# We don't enforce columns strictly (1990s code sometimes drifts) but we look
# at typical signals tolerantly. All matches case-insensitive on stripped lines.

PAT_IDENTIFICATION  = re.compile(r"^\s*IDENTIFICATION\s+DIVISION\b", re.I)
PAT_ENVIRONMENT     = re.compile(r"^\s*ENVIRONMENT\s+DIVISION\b",    re.I)
PAT_DATA_DIVISION   = re.compile(r"^\s*DATA\s+DIVISION\b",           re.I)
PAT_PROCEDURE       = re.compile(r"^\s*PROCEDURE\s+DIVISION\b",      re.I)
PAT_PROGRAM_ID      = re.compile(r"^\s*PROGRAM-ID\s*\.\s*([A-Z0-9\-_]+)", re.I)

# Copybook signals: level numbers 01..49 at the start of a content line,
# followed by a name and possibly PIC / OCCURS / REDEFINES.
PAT_LEVEL_LINE      = re.compile(r"^\s*(\d{2})\s+([A-Z0-9\-_]+)", re.I)
PAT_PIC_CLAUSE      = re.compile(r"\bPIC(?:TURE)?\s+IS?\s*[\w\(\)\.,\+\-/SVZ\*]+|\bPIC(?:TURE)?\s+[\w\(\)\.,\+\-/SVZ\*]+", re.I)
PAT_OCCURS          = re.compile(r"\bOCCURS\s+\d+", re.I)
PAT_REDEFINES       = re.compile(r"\bREDEFINES\s+[A-Z0-9\-_]+", re.I)
PAT_LEVEL_88        = re.compile(r"^\s*88\s+[A-Z0-9\-_]+", re.I)

# JCL signals
PAT_JCL_JOB         = re.compile(r"^//\S+\s+JOB\b",          re.I)
PAT_JCL_EXEC        = re.compile(r"^//\S+\s+EXEC\s+(PGM|PROC)=", re.I)
PAT_JCL_DD          = re.compile(r"^//\S+\s+DD\b",           re.I)

# Embedded technologies
PAT_EXEC_SQL        = re.compile(r"\bEXEC\s+SQL\b",     re.I)
PAT_EXEC_CICS       = re.compile(r"\bEXEC\s+CICS\b",    re.I)
PAT_COPY            = re.compile(r"^\s*COPY\s+([A-Z0-9\-_]+)", re.I)
PAT_CALL            = re.compile(r"\bCALL\s+(['\"])([A-Z0-9\-_]+)\1", re.I)

# Comment lines: '*' in column 7 (fixed-form). We tolerate the indicator
# appearing anywhere in the first 8 chars to handle slight format drift.
def is_comment_line(line: str) -> bool:
    head = line[:8]
    return "*" in head and head.strip().startswith("*")


# --------------------------------------------------------------------------- #
#  Result types                                                                 #
# --------------------------------------------------------------------------- #

@dataclass
class FileReport:
    path:            str
    size_bytes:      int
    line_count:      int
    encoding:        str
    file_type:       str               # "cobol" | "copybook" | "jcl" | "unknown"
    confidence:      str               # "high" | "medium" | "low"
    program_id:      str | None = None
    root_record:     str | None = None
    signals:         list[str] = field(default_factory=list)
    copy_includes:   list[str] = field(default_factory=list)
    static_calls:    list[str] = field(default_factory=list)
    has_exec_sql:    bool = False
    has_exec_cics:   bool = False
    comment_ratio:   float = 0.0       # share of comment lines
    first_lines:     list[str] = field(default_factory=list)  # head sample


# --------------------------------------------------------------------------- #
#  Reading with encoding tolerance                                              #
# --------------------------------------------------------------------------- #

ENCODINGS_TRIED = ("utf-8", "utf-8-sig", "cp1252", "latin-1")


def _read_text(path: Path) -> tuple[str, str]:
    """Reads the file, returns (text, encoding_used)."""
    raw = path.read_bytes()
    for enc in ENCODINGS_TRIED:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    # Last-resort: ignore errors to keep going.
    return raw.decode("latin-1", errors="replace"), "latin-1(replace)"


# --------------------------------------------------------------------------- #
#  Classifier                                                                   #
# --------------------------------------------------------------------------- #

def classify(text: str) -> tuple[str, str, list[str]]:
    """
    Returns (file_type, confidence, signals).

    Decision tree:
      1. JCL signals dominant?               -> jcl
      2. IDENTIFICATION + PROCEDURE present? -> cobol  (high confidence)
      3. Many level lines + PIC, no DIVs?    -> copybook
      4. Some level lines, ambiguous?        -> copybook (medium)
      5. Else                                -> unknown
    """
    signals: list[str] = []

    has_id     = bool(PAT_IDENTIFICATION.search(text))
    has_env    = bool(PAT_ENVIRONMENT.search(text))
    has_data   = bool(PAT_DATA_DIVISION.search(text))
    has_proc   = bool(PAT_PROCEDURE.search(text))

    jcl_hits   = sum(bool(p.search(text)) for p in (PAT_JCL_JOB, PAT_JCL_EXEC, PAT_JCL_DD))
    level_hits = len(PAT_LEVEL_LINE.findall(text))
    pic_hits   = len(PAT_PIC_CLAUSE.findall(text))
    l88_hits   = len(PAT_LEVEL_88.findall(text))

    # 1) JCL
    if jcl_hits >= 2 and not (has_id or has_proc):
        signals.append(f"jcl: {jcl_hits} statement patterns matched")
        return "jcl", "high", signals

    # 2) Full COBOL module
    if has_id and has_proc:
        signals.append("IDENTIFICATION DIVISION found")
        signals.append("PROCEDURE DIVISION found")
        if has_env:  signals.append("ENVIRONMENT DIVISION found")
        if has_data: signals.append("DATA DIVISION found")
        return "cobol", "high", signals

    # 3) COBOL minus PROCEDURE (rare but possible — outline / stub)
    if has_id and (has_env or has_data):
        signals.append("IDENTIFICATION DIVISION found (no PROCEDURE) — partial module?")
        return "cobol", "medium", signals

    # 4) Copybook — lots of level lines + PIC, no DIVISIONs
    if level_hits >= 3 and pic_hits >= 1 and not (has_id or has_proc):
        signals.append(f"copybook: {level_hits} level lines, {pic_hits} PIC clauses")
        if l88_hits: signals.append(f"{l88_hits} 88-level condition names")
        return "copybook", "high" if level_hits >= 5 else "medium", signals

    # 5) Weak copybook (only a few lines)
    if level_hits >= 1 and pic_hits >= 1:
        signals.append(f"copybook (weak): {level_hits} level lines, {pic_hits} PIC clauses")
        return "copybook", "low", signals

    signals.append(f"unrecognised — level_hits={level_hits} pic_hits={pic_hits} jcl_hits={jcl_hits}")
    return "unknown", "low", signals


# --------------------------------------------------------------------------- #
#  Per-file inspection                                                          #
# --------------------------------------------------------------------------- #

def inspect_file(path: Path) -> FileReport:
    text, encoding = _read_text(path)
    lines  = text.splitlines()

    file_type, confidence, signals = classify(text)

    # Headline metadata depending on type
    program_id  = None
    root_record = None
    m = PAT_PROGRAM_ID.search(text)
    if m:
        program_id = m.group(1).upper()

    # First non-comment level-01 line = root record (mostly for copybooks)
    for line in lines:
        if is_comment_line(line):
            continue
        ml = PAT_LEVEL_LINE.match(line)
        if ml and ml.group(1) == "01":
            root_record = ml.group(2).upper()
            break

    copy_includes = sorted({m.group(1).upper() for m in PAT_COPY.finditer(text)})
    static_calls  = sorted({m.group(2).upper() for m in PAT_CALL.finditer(text)})

    has_sql  = bool(PAT_EXEC_SQL.search(text))
    has_cics = bool(PAT_EXEC_CICS.search(text))
    if has_sql:  signals.append("EXEC SQL detected (DB2)")
    if has_cics: signals.append("EXEC CICS detected (transactional)")

    comments = sum(1 for ln in lines if is_comment_line(ln))
    ratio    = round(comments / len(lines), 2) if lines else 0.0

    return FileReport(
        path           = str(path),
        size_bytes     = path.stat().st_size,
        line_count     = len(lines),
        encoding       = encoding,
        file_type      = file_type,
        confidence     = confidence,
        program_id     = program_id,
        root_record    = root_record,
        signals        = signals,
        copy_includes  = copy_includes,
        static_calls   = static_calls,
        has_exec_sql   = has_sql,
        has_exec_cics  = has_cics,
        comment_ratio  = ratio,
        first_lines    = lines[:20],
    )


# --------------------------------------------------------------------------- #
#  Directory walker                                                             #
# --------------------------------------------------------------------------- #

DEFAULT_EXTENSIONS = (".txt", ".cbl", ".cob", ".cpy", ".jcl", ".prc")


def iter_input_files(root: Path, extensions: Iterable[str] = DEFAULT_EXTENSIONS):
    exts = {e.lower() for e in extensions}
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in exts:
            yield p


def inspect_directory(root: Path) -> list[FileReport]:
    if not root.exists():
        raise FileNotFoundError(f"Input directory not found: {root.resolve()}")

    reports: list[FileReport] = []
    for path in iter_input_files(root):
        log("inspector", f"reading {path.relative_to(root)} ({path.stat().st_size:,} bytes)")
        rep = inspect_file(path)
        log("inspector",
            f"  -> {rep.file_type:<8} [{rep.confidence}]  "
            f"{'PROGRAM-ID=' + rep.program_id if rep.program_id else ''}"
            f"{'  root=' + rep.root_record if rep.root_record else ''}")
        reports.append(rep)
    return reports


# --------------------------------------------------------------------------- #
#  Reporting                                                                    #
# --------------------------------------------------------------------------- #

def print_summary(reports: list[FileReport]) -> None:
    if not reports:
        log("inspector", "no files found")
        return

    by_type: dict[str, int] = {}
    for r in reports:
        by_type[r.file_type] = by_type.get(r.file_type, 0) + 1

    log("inspector", "─" * 70)
    log("inspector", f"Total files: {len(reports)}")
    for t, n in sorted(by_type.items(), key=lambda kv: -kv[1]):
        log("inspector", f"  {t:<10} {n}")
    log("inspector", "─" * 70)

    for r in reports:
        head = Path(r.path).name
        tag  = f"[{r.file_type}/{r.confidence}]"
        extra = []
        if r.program_id:    extra.append(f"PGM={r.program_id}")
        if r.root_record:   extra.append(f"ROOT={r.root_record}")
        if r.copy_includes: extra.append(f"COPY={len(r.copy_includes)}")
        if r.static_calls:  extra.append(f"CALL={len(r.static_calls)}")
        if r.has_exec_sql:  extra.append("SQL")
        if r.has_exec_cics: extra.append("CICS")
        log("inspector",
            f"  {head:<35} {tag:<22} {r.line_count:>6} lines  "
            + "  ".join(extra))


def save_report(reports: list[FileReport], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": {
            "total": len(reports),
            "by_type": {t: sum(1 for r in reports if r.file_type == t)
                        for t in {"cobol", "copybook", "jcl", "unknown"}},
        },
        "files": [asdict(r) for r in reports],
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    log("inspector", f"report saved to {output_path}")
