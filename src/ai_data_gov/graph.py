"""
LangGraph pipeline — AI Flow Documentation POC.

Pipeline:
  START → collector → multi_analyst → judge → validator → writer → END
                                                  ↑           |
                                                  └── retry ──┘ (max 3)
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from langgraph.graph import StateGraph, END

from src.ai_data_gov.state import FlowState
from src.ai_data_gov.agents.collector import collect
from src.ai_data_gov.agents.analyst import analyze
from src.ai_data_gov.agents.judge import judge
from src.ai_data_gov.llm import get_model


MAX_RETRIES = 3

# Max chars sent to the model (~4 chars per token, keeping 50k tokens for response)
# 262144 - 50000 = 212144 tokens * 4 = ~848576 chars
MAX_CONTEXT_CHARS = 800_000


# --------------------------------------------------------------------------- #
#  Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _add_files_with_limit(
    sections: list[str],
    files: list,
    header: str,
    budget: int,
) -> int:
    """
    Adds files to sections respecting the remaining char budget.
    Returns remaining budget after insertion.
    Truncates the last file if needed rather than skipping it entirely.
    """
    if not files or budget <= 0:
        return budget

    sections.append(header)
    for f in files:
        header_line = f"--- {f.name} ---\n"
        available   = budget - len(header_line)
        if available <= 0:
            sections.append(f"--- {f.name} --- [SKIPPED: context limit reached]")
            continue

        content  = f.content[:available]
        truncated = len(f.content) > available
        sections.append(header_line + content)
        if truncated:
            sections.append(f"[... {f.name} truncated — context limit reached]")

        budget -= len(header_line) + len(content)

    return budget


def _build_raw_context(flow_name: str, location: str | None = None) -> tuple[dict, str]:
    """
    Calls the Collector and assembles a single text context for the Analyst.
    Priority order: DDL → source → docs (most structural info first).
    Respects MAX_CONTEXT_CHARS to stay within model limits.
    Returns (counts_dict, raw_context_string).
    """
    output  = collect(flow_name)
    budget  = MAX_CONTEXT_CHARS
    sections: list[str] = []

    # Priority 1 — DDL (schema is essential)
    budget = _add_files_with_limit(sections, output.ddl_files,    "=== DDL FILES ===",              budget)

    # Priority 2 — Source code
    budget = _add_files_with_limit(sections, output.source_files, "=== SOURCE FILES ===",           budget)

    # Priority 3 — Existing docs
    budget = _add_files_with_limit(sections, output.doc_files,    "=== EXISTING DOCUMENTATION ===", budget)

    if output.errors:
        sections.append("=== COLLECTOR WARNINGS ===")
        for e in output.errors:
            sections.append(f"⚠️ {e}")

    counts = {
        "source_files_count": len(output.source_files),
        "ddl_files_count":    len(output.ddl_files),
        "doc_files_count":    len(output.doc_files),
    }

    return counts, "\n\n".join(sections)


# --------------------------------------------------------------------------- #
#  Nodes                                                                        #
# --------------------------------------------------------------------------- #

def collector_node(state: FlowState) -> dict:
    """Reads source files, DDL and docs. Builds raw context for the Analyst."""
    flow_name = state["flow_name"]
    location  = state.get("location")
    loc_label = f" [{location}]" if location else ""
    print(f"  [Collector] collecting context for flow: {flow_name}{loc_label}")

    location = state.get("location")
    counts, raw_context = _build_raw_context(flow_name, location)

    print(f"  [Collector] {counts['source_files_count']} source, "
          f"{counts['ddl_files_count']} ddl, "
          f"{counts['doc_files_count']} doc file(s)")

    return {**counts, "raw_context": raw_context}


def multi_analyst_node(state: FlowState) -> dict:
    """Runs Analyst 1 (Qwen3) and Analyst 2 (Codestral) in parallel."""
    flow_name = state["flow_name"]
    location  = state.get("location")
    attempt   = state.get("retry_count", 0) + 1

    model1 = get_model("analyst1")
    model2 = get_model("analyst2")
    print(f"  [Analysts]  running {model1} + {model2} in parallel (attempt {attempt}/{MAX_RETRIES})")

    drafts: dict = {}

    def run_analyst(role: str) -> tuple[str, str]:
        draft = analyze(
            flow_name=flow_name,
            raw_context=state["raw_context"],
            model_role=role,
            location=location,
            validation_errors=state.get("validation_errors"),
            attempt=attempt,
        )
        return role, draft

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(run_analyst, role): role for role in ["analyst1", "analyst2"]}
        for future in as_completed(futures):
            role, draft = future.result()
            model_name   = get_model(role)
            drafts[model_name] = draft
            print(f"  [Analysts]  {model_name} done ({len(draft)} chars)")

    return {
        "spec_drafts": drafts,
        "retry_count": attempt,
    }


def judge_node(state: FlowState) -> dict:
    """GPT OSS 120B synthesizes the best spec from both analyst drafts."""
    flow_name = state["flow_name"]
    drafts    = state.get("spec_drafts", {})
    model_judge = get_model("judge")

    print(f"  [Judge]     synthesizing with {model_judge}")

    draft_list = list(drafts.values())
    draft1 = draft_list[0] if len(draft_list) > 0 else ""
    draft2 = draft_list[1] if len(draft_list) > 1 else draft1

    final_spec = judge(
        flow_name=flow_name,
        draft_analyst1=draft1,
        draft_analyst2=draft2,
        location=state.get("location"),
    )

    print(f"  [Judge]     final spec ({len(final_spec)} chars)")
    return {"spec_draft": final_spec}


def validator_node(state: FlowState) -> dict:
    """Checks that all 7 required sections are present in spec_draft."""
    print(f"  [Validator] checking spec completeness")

    required_sections = [
        "## 1. Overview",
        "## 2. Source",
        "## 3. Transformation",
        "## 4. Target",
        "## 5. Lineage",
        "## 6. Quality",
        "## 7. Spring Batch",
    ]

    spec   = state.get("spec_draft", "")
    errors = [s for s in required_sections if s.lower() not in spec.lower()]

    if errors:
        print(f"  [Validator] missing sections: {errors}")
    else:
        print(f"  [Validator] all 7 sections present")

    return {
        "validation_ok":     len(errors) == 0,
        "validation_errors": errors,
    }


def writer_node(state: FlowState) -> dict:
    """Writes the final spec to output/ as a Markdown file."""
    status     = "complete" if state.get("validation_ok") else "partial"
    flow_name  = state["flow_name"]
    spec_draft = state.get("spec_draft", "")
    errors     = state.get("validation_errors", [])

    print(f"  [Writer]    writing {status} spec to output/")

    os.makedirs("output", exist_ok=True)
    loc_suffix  = f"_{state['location'].upper()}" if state.get("location") else ""
    output_path = f"output/FLOW_{flow_name}{loc_suffix}_SPEC.md"

    lines = []
    lines.append(f"# FLOW_{flow_name}_SPEC")
    lines.append(f"\n> Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> Status: {status.upper()}")

    if errors:
        lines.append("\n## ⚠️ Validation warnings")
        for e in errors:
            lines.append(f"- {e}")

    lines.append("\n---\n")
    lines.append(spec_draft if spec_draft else "_No spec generated yet._")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"  [Writer]    saved → {output_path}")
    return {"output_path": output_path}


# --------------------------------------------------------------------------- #
#  Routing                                                                      #
# --------------------------------------------------------------------------- #

def route_after_validator(state: FlowState) -> str:
    if state.get("validation_ok"):
        return "writer"

    retry_count = state.get("retry_count", 0)
    if retry_count < MAX_RETRIES:
        print(f"  [Router]    validation failed — retrying ({retry_count}/{MAX_RETRIES})")
        return "multi_analyst"

    print(f"  [Router]    max retries reached — writing partial spec")
    return "writer"


# --------------------------------------------------------------------------- #
#  Graph                                                                        #
# --------------------------------------------------------------------------- #

def build_graph() -> StateGraph:
    graph = StateGraph(FlowState)

    graph.add_node("collector",      collector_node)
    graph.add_node("multi_analyst",  multi_analyst_node)
    graph.add_node("judge",          judge_node)
    graph.add_node("validator",      validator_node)
    graph.add_node("writer",         writer_node)

    graph.set_entry_point("collector")

    graph.add_edge("collector",     "multi_analyst")
    graph.add_edge("multi_analyst", "judge")
    graph.add_edge("judge",         "validator")
    graph.add_conditional_edges(
        "validator",
        route_after_validator,
        {"multi_analyst": "multi_analyst", "writer": "writer"},
    )
    graph.add_edge("writer", END)

    return graph.compile()


app = build_graph()
