"""
LangGraph pipeline — AI Flow Documentation POC.

Pipeline:
  START → collector → analyst → validator → writer → END
                          ↑          |
                          └── retry ─┘ (max 3 attempts)

Each node is a stub in this skeleton — business logic added step by step.
"""

from langgraph.graph import StateGraph, END
from src.ai_data_gov.state import FlowState


MAX_RETRIES = 3


# --------------------------------------------------------------------------- #
#  Nodes (stubs)                                                                #
# --------------------------------------------------------------------------- #

def collector_node(state: FlowState) -> dict:
    """Reads source files, DDL and docs. Returns raw context."""
    print(f"  [Collector] collecting context for flow: {state['flow_name']}")

    # TODO: call collect(state["flow_name"]) and build raw_context
    return {
        "source_files_count": 0,
        "ddl_files_count":    0,
        "doc_files_count":    0,
        "raw_context":        f"[STUB] raw context for {state['flow_name']}",
    }


def analyst_node(state: FlowState) -> dict:
    """Calls Qwen3 to generate the flow spec from raw context."""
    attempt = state.get("retry_count", 0) + 1
    print(f"  [Analyst]   generating spec (attempt {attempt}/{MAX_RETRIES})")

    # TODO: call Qwen3 with raw_context and return spec_draft
    return {
        "spec_draft":  f"[STUB] spec draft for {state['flow_name']} — attempt {attempt}",
        "retry_count": attempt,
    }


def validator_node(state: FlowState) -> dict:
    """Checks that all 7 required sections are present in spec_draft."""
    print(f"  [Validator] checking spec completeness")

    # TODO: check 7 sections and return validation_ok + validation_errors
    return {
        "validation_ok":     True,
        "validation_errors": [],
    }


def writer_node(state: FlowState) -> dict:
    """Writes the final spec to output/ as a Markdown file."""
    import os
    from datetime import datetime

    status     = "complete" if state.get("validation_ok") else "partial"
    flow_name  = state["flow_name"]
    spec_draft = state.get("spec_draft", "")
    errors     = state.get("validation_errors", [])

    print(f"  [Writer]    writing {status} spec to output/")

    os.makedirs("output", exist_ok=True)
    output_path = f"output/FLOW_{flow_name}_SPEC.md"

    # Build the file content
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
    return {
        "output_path": output_path,
    }


# --------------------------------------------------------------------------- #
#  Routing                                                                      #
# --------------------------------------------------------------------------- #

def route_after_validator(state: FlowState) -> str:
    """
    After validation:
      - OK               → writer
      - KO + retries left → analyst (retry)
      - KO + max retries  → writer  (partial spec)
    """
    if state.get("validation_ok"):
        return "writer"

    retry_count = state.get("retry_count", 0)
    if retry_count < MAX_RETRIES:
        print(f"  [Router]    validation failed — retrying ({retry_count}/{MAX_RETRIES})")
        return "analyst"

    print(f"  [Router]    max retries reached — writing partial spec")
    return "writer"


# --------------------------------------------------------------------------- #
#  Graph definition                                                             #
# --------------------------------------------------------------------------- #

def build_graph() -> StateGraph:
    graph = StateGraph(FlowState)

    graph.add_node("collector", collector_node)
    graph.add_node("analyst",   analyst_node)
    graph.add_node("validator", validator_node)
    graph.add_node("writer",    writer_node)

    graph.set_entry_point("collector")

    graph.add_edge("collector", "analyst")
    graph.add_edge("analyst",   "validator")
    graph.add_conditional_edges(
        "validator",
        route_after_validator,
        {"analyst": "analyst", "writer": "writer"},
    )
    graph.add_edge("writer", END)

    return graph.compile()


# Compiled app — import this in main.py and tests
app = build_graph()
