"""
LangGraph pipeline — AI Flow Documentation POC.

Pipeline:
  START → collector → analyst → validator → writer → END
                          ↑          |
                          └── retry ─┘ (max 3 attempts)
"""

import os
from datetime import datetime
from langgraph.graph import StateGraph, END

from src.ai_data_gov.state import FlowState
from src.ai_data_gov.agents.collector import collect
from src.ai_data_gov.llm import build_client, get_model
from src.ai_data_gov.prompt import SYSTEM_PROMPT, build_user_prompt


MAX_RETRIES = 3


# --------------------------------------------------------------------------- #
#  Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _build_raw_context(flow_name: str) -> tuple[dict, str]:
    """
    Calls the Collector and assembles a single text context for the Analyst.
    Returns (counts_dict, raw_context_string).
    """
    output = collect(flow_name)

    sections = []

    if output.source_files:
        sections.append("=== SOURCE FILES ===")
        for f in output.source_files:
            sections.append(f"--- {f.name} ---")
            sections.append(f.content)

    if output.ddl_files:
        sections.append("=== DDL FILES ===")
        for f in output.ddl_files:
            sections.append(f"--- {f.name} ---")
            sections.append(f.content)

    if output.doc_files:
        sections.append("=== EXISTING DOCUMENTATION ===")
        for f in output.doc_files:
            sections.append(f"--- {f.name} ---")
            sections.append(f.content)

    if output.errors:
        sections.append("=== COLLECTOR WARNINGS ===")
        for e in output.errors:
            sections.append(f"⚠️ {e}")

    counts = {
        "source_files_count": output.file_count if hasattr(output, "file_count") else len(output.source_files),
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
    print(f"  [Collector] collecting context for flow: {flow_name}")

    counts, raw_context = _build_raw_context(flow_name)

    print(f"  [Collector] {counts['source_files_count']} source, "
          f"{counts['ddl_files_count']} ddl, "
          f"{counts['doc_files_count']} doc file(s)")

    return {**counts, "raw_context": raw_context}


def analyst_node(state: FlowState) -> dict:
    """Calls Qwen3 to generate the flow spec from raw context."""
    attempt   = state.get("retry_count", 0) + 1
    flow_name = state["flow_name"]
    print(f"  [Analyst]   generating spec (attempt {attempt}/{MAX_RETRIES})")

    # Append validator feedback to the prompt on retries
    user_content = build_user_prompt(flow_name, state["raw_context"])
    if attempt > 1 and state.get("validation_errors"):
        feedback = "\n".join(state["validation_errors"])
        user_content += (
            f"\n\n⚠️ Previous attempt was incomplete. Fix these issues:\n{feedback}"
        )

    client = build_client()
    response = client.chat.completions.create(
        model=get_model(),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_content},
        ],
        temperature=0.2,
    )

    spec_draft = response.choices[0].message.content.strip()
    print(f"  [Analyst]   spec generated ({len(spec_draft)} chars)")

    return {
        "spec_draft":  spec_draft,
        "retry_count": attempt,
    }


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
    output_path = f"output/FLOW_{flow_name}_SPEC.md"

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
        return "analyst"

    print(f"  [Router]    max retries reached — writing partial spec")
    return "writer"


# --------------------------------------------------------------------------- #
#  Graph                                                                        #
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


app = build_graph()
