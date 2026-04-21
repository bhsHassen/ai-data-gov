"""
Shared state flowing through the LangGraph pipeline.
Every agent reads from and writes to this state.
"""
from __future__ import annotations

from typing import TypedDict, Optional


class FlowState(TypedDict):
    # Input
    flow_name: str                  # e.g. "TIERS_LEI"
    location: Optional[str]         # e.g. "Sydney", "London" (optional)

    # Collector output
    source_files_count: int         # number of source files collected
    ddl_files_count: int            # number of DDL files collected
    doc_files_count: int            # number of doc files collected
    raw_context: str                # full text context passed to Analyst

    # Analyst output
    spec_drafts: dict               # {model_name: draft} from each analyst
    spec_draft: str                 # final spec after judge synthesis

    # Validator output
    validation_ok: bool             # True if all 7 sections present
    validation_errors: list[str]    # missing or incomplete sections

    # Control
    retry_count: int                # current retry count (max 3)
    pipeline_mode: str              # "single" (Qwen3 only) | "multi" (Qwen3 + Codestral + Judge)

    # Writer output
    output_path: Optional[str]      # path to the written .md file
