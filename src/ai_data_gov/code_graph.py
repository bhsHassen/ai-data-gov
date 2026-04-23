"""
LangGraph pipeline — Code Generation (Spring Batch 5 / Java 17).

Linear pipeline, no retry:

    START → loader → developer → reviewer → code_writer → END

The loader reads a previously generated flow spec from output/, splits it
into numbered sections, and detects whether the flow is file-based.
Developer drafts the Java code, Reviewer rewrites it, and code_writer
materialises the files under code_output/<FLOW>/.
"""
from __future__ import annotations

from langgraph.graph import StateGraph, END

from src.ai_data_gov.code_state import CodeGenState
from src.ai_data_gov.agents.spec_loader import load_spec, detect_file_flow, load_guideline
from src.ai_data_gov.agents.developer import develop
from src.ai_data_gov.agents.reviewer import review
from src.ai_data_gov.agents.code_writer import parse_files, write_files
from src.ai_data_gov.llm import get_model
from src.ai_data_gov.console import log, emit_event


# --------------------------------------------------------------------------- #
#  Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _emit_code_files(stage: str, delimited: str) -> int:
    """
    Parses a delimited blob and emits a `code_file` event per file so the
    dashboard can upsert a live tab. Returns the number of files emitted.
    """
    files = parse_files(delimited)
    for filename, content in files:
        emit_event({
            "type":     "code_file",
            "stage":    stage,
            "filename": filename,
            "content":  content,
        })
    return len(files)


# --------------------------------------------------------------------------- #
#  Nodes                                                                        #
# --------------------------------------------------------------------------- #

def loader_node(state: CodeGenState) -> dict:
    """
    Reads the spec file from output/ and extracts its numbered sections.
    Also loads the project guideline (./guideline.md) if present — the
    Developer and Reviewer both receive it as architectural ground-truth.
    """
    spec_filename = state["spec_filename"]

    emit_event({"type": "stage_start", "stage": "loader"})
    log("loader", f"parsing spec: {spec_filename}")

    sections     = load_spec(spec_filename)
    is_file_flow = detect_file_flow(sections.get("2", ""))
    guideline    = load_guideline()

    if guideline:
        log("loader", f"guideline loaded ({len(guideline):,} chars)")
    else:
        log("loader", "no guideline.md found — using defaults")

    section_keys = sorted(k for k in sections.keys() if k != "raw")
    kind         = "file flow" if is_file_flow else "DB flow"
    gl_note      = f" · guideline {len(guideline):,} chars" if guideline else " · no guideline"
    detail       = f"{len(section_keys)} sections · {kind}{gl_note}"
    log("loader", detail)
    emit_event({"type": "stage_done", "stage": "loader", "detail": detail})

    return {
        "spec_markdown": sections.get("raw", ""),
        "spec_sections": sections,
        "is_file_flow":  is_file_flow,
        "guideline":     guideline,
    }


def developer_node(state: CodeGenState) -> dict:
    """Calls the Developer model to draft the Spring Batch code."""
    model = get_model("developer")

    emit_event({"type": "stage_start", "stage": "developer",
                "detail": f"{model} · attempt 1/1"})
    log("developer", f"calling {model}")

    dev_code = develop(
        flow_name     = state["flow_name"],
        spec_markdown = state["spec_markdown"],
        is_file_flow  = state["is_file_flow"],
        guideline     = state.get("guideline", ""),
    )

    n_files = _emit_code_files("developer", dev_code)
    detail  = f"{n_files} files · {len(dev_code):,} chars"
    log("developer", detail)
    emit_event({"type": "stage_done", "stage": "developer", "detail": detail})

    return {"dev_code": dev_code}


def reviewer_node(state: CodeGenState) -> dict:
    """Calls the Reviewer model to rewrite the Developer output."""
    model = get_model("reviewer")

    emit_event({"type": "stage_start", "stage": "reviewer",
                "detail": f"{model} · attempt 1/1"})
    log("reviewer", f"calling {model}")

    final_code = review(
        flow_name     = state["flow_name"],
        spec_markdown = state["spec_markdown"],
        dev_code      = state["dev_code"],
        guideline     = state.get("guideline", ""),
    )

    n_files = _emit_code_files("reviewer", final_code)
    detail  = f"{n_files} files · {len(final_code):,} chars"
    log("reviewer", detail)
    emit_event({"type": "stage_done", "stage": "reviewer", "detail": detail})

    return {"final_code": final_code}


def code_writer_node(state: CodeGenState) -> dict:
    """Writes the final Java files to code_output/<FLOW>/."""
    emit_event({"type": "stage_start", "stage": "code_writer"})
    log("code_writer", "writing files to disk")

    result = write_files(
        flow_name     = state["flow_name"],
        final_code    = state["final_code"],
        spec_filename = state["spec_filename"],
    )

    for path in result["output_paths"]:
        log("code_writer", f"saved → {path}")

    detail = result["output_dir"]
    emit_event({"type": "stage_done", "stage": "code_writer", "detail": detail})
    emit_event({
        "type":       "pipeline_complete",
        "output_dir": result["output_dir"],
        "files":      result["filenames"],
    })

    return {
        "output_dir":   result["output_dir"],
        "output_paths": result["output_paths"],
        "filenames":    result["filenames"],
    }


# --------------------------------------------------------------------------- #
#  Graph                                                                        #
# --------------------------------------------------------------------------- #

def build_code_graph() -> StateGraph:
    graph = StateGraph(CodeGenState)

    graph.add_node("loader",      loader_node)
    graph.add_node("developer",   developer_node)
    graph.add_node("reviewer",    reviewer_node)
    graph.add_node("code_writer", code_writer_node)

    graph.set_entry_point("loader")
    graph.add_edge("loader",      "developer")
    graph.add_edge("developer",   "reviewer")
    graph.add_edge("reviewer",    "code_writer")
    graph.add_edge("code_writer", END)

    return graph.compile()


code_app = build_code_graph()
