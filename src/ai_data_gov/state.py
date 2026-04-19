"""
Shared state flowing through the LangGraph pipeline.
Every agent reads from and writes to this state.
"""

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
    spec_draft: str                 # generated spec (Markdown)

    # Validator output
    validation_ok: bool             # True if all 7 sections present
    validation_errors: list[str]    # missing or incomplete sections

    # Control
    retry_count: int                # current retry count (max 3)

    # Writer output
    output_path: Optional[str]      # path to the written .md file
