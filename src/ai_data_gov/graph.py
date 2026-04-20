"""
LangGraph pipeline — AI Flow Documentation POC.

Pipeline:
  START → collector → multi_analyst → judge → self_review → validator → writer → END
                             ↑                                    |
                             └────────────── retry ───────────────┘ (max 3)
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from langgraph.graph import StateGraph, END

from src.ai_data_gov.state import FlowState
from src.ai_data_gov.agents.collector import collect
from src.ai_data_gov.agents.analyst import analyze
from src.ai_data_gov.agents.judge import judge, self_review
from src.ai_data_gov.agents.validator import validate
from src.ai_data_gov.agents.writer import write
from src.ai_data_gov.llm import get_model
from src.ai_data_gov.console import log, emit_event


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

    emit_event({"type": "stage_start", "stage": "collector"})
    log("collector", f"collecting context for: {flow_name}{loc_label}")

    counts, raw_context = _build_raw_context(flow_name, location)

    detail = (f"{counts['source_files_count']} source · "
              f"{counts['ddl_files_count']} DDL · "
              f"{counts['doc_files_count']} doc")
    log("collector", detail + " file(s)")
    emit_event({"type": "stage_done", "stage": "collector", "detail": detail})

    return {**counts, "raw_context": raw_context}


def multi_analyst_node(state: FlowState) -> dict:
    """Runs Analyst 1 (Qwen3) and Analyst 2 (Codestral) in parallel."""
    flow_name = state["flow_name"]
    location  = state.get("location")
    attempt   = state.get("retry_count", 0) + 1

    model1 = get_model("analyst1")
    model2 = get_model("analyst2")
    emit_event({"type": "stage_start", "stage": "analyst",
                "detail": f"{model1} + {model2}  ·  attempt {attempt}/{MAX_RETRIES}"})
    log("analyst", f"running {model1} + {model2} in parallel (attempt {attempt}/{MAX_RETRIES})")

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
            log("analyst", f"{model_name} done ({len(draft)} chars)")

    sizes = " · ".join(f"{m}: {len(d):,} chars" for m, d in drafts.items())
    emit_event({"type": "stage_done", "stage": "analyst", "detail": sizes})
    return {
        "spec_drafts": drafts,
        "retry_count": attempt,
    }


def judge_node(state: FlowState) -> dict:
    """GPT OSS 120B synthesizes the best spec from both analyst drafts."""
    flow_name = state["flow_name"]
    drafts    = state.get("spec_drafts", {})
    model_judge = get_model("judge")

    emit_event({"type": "stage_start", "stage": "judge", "detail": model_judge})
    log("judge", f"synthesizing with {model_judge}")

    draft_list = list(drafts.values())
    draft1 = draft_list[0] if len(draft_list) > 0 else ""
    draft2 = draft_list[1] if len(draft_list) > 1 else draft1

    final_spec = judge(
        flow_name=flow_name,
        raw_context=state["raw_context"],
        draft_analyst1=draft1,
        draft_analyst2=draft2,
        location=state.get("location"),
    )

    log("judge", f"first draft ({len(final_spec)} chars)")
    emit_event({"type": "stage_done", "stage": "judge",
                "detail": f"{len(final_spec):,} chars"})
    return {"spec_draft": final_spec}


def self_review_node(state: FlowState) -> dict:
    """Judge reviews and improves its own spec against the source artifacts."""
    emit_event({"type": "stage_start", "stage": "self_review"})
    log("judge", "self-review — improving spec against source artifacts")

    improved = self_review(
        flow_name=state["flow_name"],
        raw_context=state["raw_context"],
        spec_draft=state["spec_draft"],
        location=state.get("location"),
    )

    log("judge", f"improved spec ({len(improved)} chars)")
    emit_event({"type": "stage_done", "stage": "self_review",
                "detail": f"{len(improved):,} chars"})
    return {"spec_draft": improved}


def validator_node(state: FlowState) -> dict:
    """Checks that all 7 required sections are present in spec_draft."""
    emit_event({"type": "stage_start", "stage": "validator"})
    log("validator", "checking spec completeness")

    ok, missing = validate(state.get("spec_draft", ""))

    if missing:
        log("validator", f"missing sections: {missing}")
    else:
        log("validator", "all 7 sections present ✓")

    detail = "all 7 sections ✓" if ok else f"missing: {', '.join(missing)}"
    emit_event({"type": "stage_done", "stage": "validator",
                "detail": detail, "ok": ok})
    return {
        "validation_ok":     ok,
        "validation_errors": missing,
    }


def writer_node(state: FlowState) -> dict:
    """Writes the final spec to output/ as a Markdown file."""
    status = "complete" if state.get("validation_ok") else "partial"
    emit_event({"type": "stage_start", "stage": "writer"})
    log("writer", f"writing {status} spec")

    output_path = write(
        flow_name=state["flow_name"],
        spec_draft=state.get("spec_draft", ""),
        validation_ok=state.get("validation_ok", False),
        validation_errors=state.get("validation_errors", []),
        location=state.get("location"),
    )

    log("writer", f"saved → {output_path}")
    emit_event({"type": "stage_done", "stage": "writer", "detail": str(output_path)})
    emit_event({"type": "pipeline_complete", "output_path": str(output_path),
                "validation_ok": state.get("validation_ok", False)})
    return {"output_path": output_path}


# --------------------------------------------------------------------------- #
#  Routing                                                                      #
# --------------------------------------------------------------------------- #

def route_after_judge(state: FlowState) -> str:
    if state.get("self_review_enabled", True):
        return "self_review"
    log("router", "self-review disabled — skipping to validator")
    return "validator"


def route_after_validator(state: FlowState) -> str:
    if state.get("validation_ok"):
        return "writer"

    retry_count = state.get("retry_count", 0)
    if retry_count < MAX_RETRIES:
        log("router", f"validation failed — retrying ({retry_count}/{MAX_RETRIES})")
        emit_event({"type": "retry", "retry_count": retry_count, "max": MAX_RETRIES})
        return "multi_analyst"

    log("router", "max retries reached — writing partial spec")
    return "writer"


# --------------------------------------------------------------------------- #
#  Graph                                                                        #
# --------------------------------------------------------------------------- #

def build_graph() -> StateGraph:
    graph = StateGraph(FlowState)

    graph.add_node("collector",      collector_node)
    graph.add_node("multi_analyst",  multi_analyst_node)
    graph.add_node("judge",          judge_node)
    graph.add_node("self_review",    self_review_node)
    graph.add_node("validator",      validator_node)
    graph.add_node("writer",         writer_node)

    graph.set_entry_point("collector")

    graph.add_edge("collector",     "multi_analyst")
    graph.add_edge("multi_analyst", "judge")
    graph.add_conditional_edges(
        "judge",
        route_after_judge,
        {"self_review": "self_review", "validator": "validator"},
    )
    graph.add_edge("self_review",   "validator")
    graph.add_conditional_edges(
        "validator",
        route_after_validator,
        {"multi_analyst": "multi_analyst", "writer": "writer"},
    )
    graph.add_edge("writer", END)

    return graph.compile()


app = build_graph()
