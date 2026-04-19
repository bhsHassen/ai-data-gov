"""
Analyst agent — calls Qwen3 to generate a data flow specification.

Receives the initial context from the Collector.
If more files are needed, uses the get_file tool to fetch them.
Produces a structured Markdown spec with 7 sections.
"""
from __future__ import annotations

import json
from src.ai_data_gov.llm import build_client, get_model
from src.ai_data_gov.prompt import SYSTEM_PROMPT, build_user_prompt
from src.ai_data_gov.agents.collector import get_file
from src.ai_data_gov.console import log


# Tool definition exposed to Qwen3
GET_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "get_file",
        "description": (
            "Fetches the full content of a source file by filename. "
            "Use this when you need a specific file that was not provided "
            "in the initial context to complete your analysis."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Exact filename to retrieve (e.g. TiersLEIImportWork.java)",
                }
            },
            "required": ["filename"],
        },
    },
}


def analyze(
    flow_name: str,
    raw_context: str,
    model_role: str = "analyst1",
    location: str | None = None,
    validation_errors: list[str] | None = None,
    attempt: int = 1,
) -> str:
    """
    Calls Qwen3 with tool use to generate the flow spec.

    The model can call get_file(filename) if it needs additional files.
    The loop continues until the model returns a final text response.

    Args:
        flow_name:         Name of the flow (e.g. "TIERS_LEI").
        raw_context:       Initial context from the Collector.
        validation_errors: Missing sections flagged by Validator (retry only).
        attempt:           Current attempt number.

    Returns:
        spec_draft: Markdown string with 7 sections.
    """
    client = build_client()
    model  = get_model(model_role)

    user_content = build_user_prompt(flow_name, raw_context, location)

    if attempt > 1 and validation_errors:
        feedback = "\n".join(validation_errors)
        user_content += (
            f"\n\n⚠️ Previous attempt was incomplete. "
            f"Make sure to include these missing sections:\n{feedback}"
        )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": user_content},
    ]

    max_tool_calls = 5   # safety limit — avoid infinite tool loops
    tool_calls_made = 0

    while True:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=[GET_FILE_TOOL],
            tool_choice="auto",
            temperature=0.2,
        )

        message = response.choices[0].message

        # Model returned a final answer — done
        if not message.tool_calls:
            return message.content.strip()

        # Model requested files — execute and feed back results
        messages.append(message)

        for tool_call in message.tool_calls:
            filename = json.loads(tool_call.function.arguments).get("filename", "")
            log("analyst", f"requesting file: {filename}")

            file_content = get_file(filename)

            messages.append({
                "role":         "tool",
                "tool_call_id": tool_call.id,
                "content":      file_content,
            })
            tool_calls_made += 1

        # Safety: stop tool loop if limit reached
        if tool_calls_made >= max_tool_calls:
            log("analyst", f"tool call limit reached ({max_tool_calls}), finalizing")
            messages.append({
                "role":    "user",
                "content": "You have reached the file request limit. Generate the spec now with what you have.",
            })
