"""
Analyst agent — calls Qwen3 to generate a data flow specification.

Receives raw context (source code, DDL, docs) from the Collector
and produces a structured Markdown spec with 7 sections.
On retry, validator feedback is appended to the prompt.
"""

from src.ai_data_gov.llm import build_client, get_model
from src.ai_data_gov.prompt import SYSTEM_PROMPT, build_user_prompt


def analyze(
    flow_name: str,
    raw_context: str,
    validation_errors: list[str] | None = None,
    attempt: int = 1,
) -> str:
    """
    Calls Qwen3 and returns the generated spec as a Markdown string.

    Args:
        flow_name:         Name of the flow (e.g. "TIERS_LEI").
        raw_context:       Concatenated source files, DDL and docs.
        validation_errors: Sections flagged as missing by the Validator (retry only).
        attempt:           Current attempt number (used for logging).

    Returns:
        spec_draft: Markdown string with 7 sections.
    """
    user_content = build_user_prompt(flow_name, raw_context)

    # On retries, append validator feedback so Qwen3 knows what to fix
    if attempt > 1 and validation_errors:
        feedback = "\n".join(validation_errors)
        user_content += (
            f"\n\n⚠️ Previous attempt was incomplete. "
            f"Make sure to include these missing sections:\n{feedback}"
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

    return response.choices[0].message.content.strip()
