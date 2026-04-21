"""
Writer agent — writes the final spec to output/ as a Markdown file.

Produces a Confluence-ready .md file named FLOW_<NAME>_SPEC.md
or FLOW_<NAME>_<LOCATION>_SPEC.md when a location is specified.
"""
from __future__ import annotations

import os
from datetime import datetime


def write(
    flow_name: str,
    spec_draft: str,
    validation_ok: bool,
    validation_errors: list[str],
    location: str | None = None,
    output_dir: str = "output",
) -> str:
    """
    Writes the spec to a Markdown file.

    Args:
        flow_name:         Name of the flow (e.g. "ATLAS2").
        spec_draft:        Final Markdown spec from the Judge.
        validation_ok:     True if all 7 sections passed validation.
        validation_errors: List of missing sections (if any).
        location:          Optional location (e.g. "Sydney").
        output_dir:        Output directory (default: "output").

    Returns:
        output_path: Absolute path of the written file.
    """
    os.makedirs(output_dir, exist_ok=True)

    status     = "COMPLETE" if validation_ok else "PARTIAL"
    loc_suffix = f"_{location.upper()}" if location else ""
    filename   = f"FLOW_{flow_name}{loc_suffix}_SPEC.md"
    output_path = os.path.join(output_dir, filename)

    lines = []
    lines.append(f"# FLOW_{flow_name}{loc_suffix}_SPEC")
    lines.append(f"\n> **Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> **Status:** {status}")
    if location:
        lines.append(f"> **Location:** {location}")

    if validation_errors:
        lines.append("\n---")
        lines.append("\n> ⚠️ **Incomplete spec** — the following sections are missing:")
        for err in validation_errors:
            lines.append(f"> - {err}")

    lines.append("\n---\n")
    lines.append(spec_draft if spec_draft else "_No spec was generated._")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return output_path
