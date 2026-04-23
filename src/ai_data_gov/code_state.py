"""
Shared state flowing through the code-generation LangGraph pipeline.

Pipeline: loader → developer → reviewer → code_writer → END
Linear — no retry loop, no validator. The Reviewer rewrites directly.
"""
from __future__ import annotations

from typing import TypedDict, Optional


class CodeGenState(TypedDict):
    # ── INPUT ────────────────────────────────────────────────────────────
    flow_name:       str   # e.g. "ATLAS2" — derived from spec filename
    spec_filename:   str   # e.g. "FLOW_ATLAS2_SPEC.md"

    # ── LOADER OUTPUT ────────────────────────────────────────────────────
    spec_markdown:   str                # full spec content as read from disk
    spec_sections:   dict               # {"1": "...", ..., "7": "...", "raw": "..."}
    is_file_flow:    bool               # True if Section 2 has an Offset column
    guideline:       str                # content of guideline.md — target
                                        # architecture + naming + conventions
                                        # (empty string if no file present)

    # ── DEVELOPER OUTPUT ─────────────────────────────────────────────────
    dev_code:        str                # delimited string from the Developer

    # ── REVIEWER OUTPUT ──────────────────────────────────────────────────
    final_code:      str                # delimited string after review

    # ── WRITER OUTPUT ────────────────────────────────────────────────────
    output_dir:      Optional[str]      # e.g. "code_output/ATLAS2"
    output_paths:    list[str]          # absolute paths of the written files
    filenames:       list[str]          # just the filenames, in order
