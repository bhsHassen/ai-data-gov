"""
Judge agent — synthesizes the best spec from multiple analyst drafts.

Receives drafts from Analyst 1 (Qwen3) and Analyst 2 (Codestral).
Uses GPT OSS 120B to compare and produce a superior final spec.
"""
from __future__ import annotations

from src.ai_data_gov.llm import build_client, get_model
from src.ai_data_gov.prompt import SYSTEM_PROMPT


JUDGE_PROMPT = """You are a senior data governance expert and technical reviewer at a global investment bank.

You have received two independent specifications for the same data flow from two analysts.

## YOUR TASK
Produce a single SUPERIOR specification by taking the best of both drafts.

## SYNTHESIS RULES
- **Precision wins**: prefer the most specific version (exact field names, exact class names, exact business rules)
- **Coverage wins**: if one analyst captured something the other missed, include it
- **Disagreement**: if the two drafts contradict, pick the most technically grounded version and flag with ⚠️
- **Never invent**: if neither analyst found the information, write `[INFORMATION NOT FOUND — source required]`

## FORMAT RULES — same as the analysts
- Confluence-ready Markdown (tables, bold, bullet points — no raw code)
- Confidence indicators on every transformation: ✅ ⚠️ ❓
- Ignore common technical fields (audit, batch infrastructure, generic flags)
- Section 7 = implementation guidelines only, no source code
- Section 5 Lineage = business story first, then summary table

## OUTPUT
Produce all 7 sections in order. The result must be publishable on Confluence as-is.
"""


def judge(
    flow_name: str,
    draft_analyst1: str,
    draft_analyst2: str,
    location: str | None = None,
) -> str:
    """
    Synthesizes the best spec from two analyst drafts.

    Args:
        flow_name:      Name of the flow.
        draft_analyst1: Spec from Analyst 1 (Qwen3).
        draft_analyst2: Spec from Analyst 2 (Codestral).
        location:       Optional location context.

    Returns:
        Final synthesized spec as Markdown string.
    """
    loc_note = f" for location: {location}" if location else ""

    user_content = f"""Flow: {flow_name}{loc_note}

=== ANALYST 1 DRAFT (Qwen3) ===
{draft_analyst1}

=== ANALYST 2 DRAFT (Codestral) ===
{draft_analyst2}

Synthesize the best possible specification from these two drafts.
"""

    client = build_client()
    response = client.chat.completions.create(
        model=get_model("judge"),
        messages=[
            {"role": "system", "content": JUDGE_PROMPT},
            {"role": "user",   "content": user_content},
        ],
        temperature=0.1,
    )

    return response.choices[0].message.content.strip()
