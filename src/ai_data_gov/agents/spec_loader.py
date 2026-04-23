"""
Spec loader — reads a generated FLOW_<NAME>_SPEC.md file and splits it
into its 7 numbered sections.

Also detects whether the flow is a file-based flow (fixed-width, Offset column
present in Section 2) or a DB-to-DB flow.
"""
from __future__ import annotations

import re
from pathlib import Path


OUTPUT_DIR = Path("output")

# Matches a level-2 heading like "## 2. Source" → captures "2"
_SECTION_HEADING = re.compile(r"^##\s*(\d+)\.\s", re.MULTILINE)


def load_spec(filename: str, base_dir: Path = OUTPUT_DIR) -> dict:
    """
    Reads a spec file and splits it on level-2 numbered headings.

    Args:
        filename: e.g. "FLOW_ATLAS2_SPEC.md". Must not contain ".." and must
                  end with ".md" — mirrors the guard used in dashboard._load_md.
        base_dir: directory containing the spec (default: ./output).

    Returns:
        {
            "raw": "<full markdown>",
            "1":   "<content of section 1>",
            ...
            "7":   "<content of section 7>",
        }
        Sections not found are simply absent from the dict.

    Raises:
        ValueError: if filename fails the path-safety guard.
        FileNotFoundError: if the file does not exist in base_dir.
    """
    if ".." in filename or not filename.endswith(".md"):
        raise ValueError(f"Invalid spec filename: {filename!r}")

    path = base_dir / filename
    if not path.exists():
        raise FileNotFoundError(f"Spec not found: {path}")

    raw = path.read_text(encoding="utf-8")

    sections: dict = {"raw": raw}
    matches = list(_SECTION_HEADING.finditer(raw))

    for i, m in enumerate(matches):
        number  = m.group(1)
        start   = m.start()
        end     = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        sections[number] = raw[start:end].strip()

    return sections


def detect_file_flow(section2: str) -> bool:
    """
    Heuristic — a flow is a file (fixed-width) flow if Section 2's table
    header contains an `Offset` column. DB-to-DB flows use `N/A` in that
    column and the header still contains "Offset", so we also check that
    the column has at least one numeric value.
    """
    if not section2:
        return False

    # Locate a markdown table header that mentions "Offset" (case-insensitive).
    has_offset_header = bool(re.search(r"\|\s*Offset\s*\|", section2, re.IGNORECASE))
    if not has_offset_header:
        return False

    # Check that at least one data row has a numeric offset (not "N/A").
    # Very simple: any line like "| ... | 0 | ..." or "| ... | 12 | ..."
    for line in section2.splitlines():
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.split("|")]
        for cell in cells:
            if re.fullmatch(r"\d+", cell):
                return True

    return False


_GUIDELINE_CANDIDATES = (
    "guideline.md",
    "guidelines.md",
    "GUIDELINE.md",
    "Guideline.md",
    "docs/guideline.md",
    "docs/guidelines.md",
    "docs/GUIDELINE.md",
)


def _found_guideline_path(custom_path: str | None) -> Path | None:
    """Returns the first existing candidate path, or None."""
    if custom_path:
        p = Path(custom_path)
        return p if p.exists() else None
    for name in _GUIDELINE_CANDIDATES:
        p = Path(name)
        if p.exists():
            return p
    return None


def load_guideline(path: str | None = None) -> tuple[str, str]:
    """
    Reads the project guideline file.

    The guideline describes the target architecture (package layout, base
    classes to extend, logging conventions, error-handling style, naming
    rules, how a new migration session should be structured...). It is
    project-wide — the same file applies to every flow.

    Lookup order when `path` is not supplied:
      1. ./guideline.md       (recommended)
      2. ./guidelines.md
      3. ./GUIDELINE.md  /  ./Guideline.md
      4. ./docs/guideline.md
      5. ./docs/guidelines.md  /  ./docs/GUIDELINE.md

    Returns a tuple (content, diagnostic):
      - content: file text, or "" if no candidate was found / readable.
      - diagnostic: human-readable string for the log. When found,
        includes the resolved absolute path + char count. When not found,
        lists the absolute paths that were checked.

    Missing guideline is NOT an error — the pipeline falls back to the
    defaults baked into the Developer / Reviewer system prompts.
    """
    found = _found_guideline_path(path)

    if found is None:
        if path:
            tried = [str(Path(path).resolve())]
        else:
            tried = [str(Path(c).resolve()) for c in _GUIDELINE_CANDIDATES]
        return "", "no guideline found — checked: " + ", ".join(tried)

    try:
        content = found.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Fallback for BOM-ed / CP1252 files so a bad encoding doesn't
        # silently kill the feature.
        try:
            content = found.read_text(encoding="utf-8-sig")
        except Exception as e:  # noqa: BLE001
            return "", f"guideline at {found.resolve()} could not be read: {e}"
    except Exception as e:  # noqa: BLE001
        return "", f"guideline at {found.resolve()} could not be read: {e}"

    return content, f"guideline loaded ({len(content):,} chars) from {found.resolve()}"


def derive_flow_name(filename: str) -> str:
    """
    Derives the flow name from a spec filename.

    Examples:
        FLOW_ATLAS2_SPEC.md         -> "ATLAS2"
        FLOW_ATLAS2_SYDNEY_SPEC.md  -> "ATLAS2"  (location suffix is ambiguous —
                                                 caller should pass flow_name
                                                 explicitly when known)

    Note: for filenames with a LOCATION suffix this returns the first token
    after FLOW_. The dashboard derives flow_name from the UI where possible.
    """
    stem = filename
    if stem.startswith("FLOW_"):
        stem = stem[len("FLOW_"):]
    if stem.endswith("_SPEC.md"):
        stem = stem[: -len("_SPEC.md")]
    # Split on underscore — first token is the flow, rest (if any) is location.
    parts = stem.split("_")
    return parts[0] if parts else stem
