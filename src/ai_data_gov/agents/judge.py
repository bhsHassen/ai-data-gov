"""
Judge agent — synthesizes the best spec from multiple analyst drafts.

Receives drafts from Analyst 1 (Qwen3) and Analyst 2 (Codestral).
Uses GPT OSS 120B to compare and produce a superior final spec.
"""
from __future__ import annotations

from src.ai_data_gov.llm import build_client, get_model
from src.ai_data_gov.prompt import SYSTEM_PROMPT


JUDGE_PROMPT = """You are a senior data governance expert and technical reviewer.

You have received two independent specifications for the same data flow, written by two different analysts.

Your task:
1. Read both specs carefully
2. Identify what each analyst captured correctly and precisely
3. Identify gaps or inaccuracies in each spec
4. Produce a single SUPERIOR specification that combines the best of both

The final spec MUST contain all 7 sections:
## 1. Overview
## 2. Source
## 3. Transformation
## 4. Target
## 5. Lineage
## 6. Quality
## 7. Spring Batch

Rules:
- Prefer the most precise and specific information (exact field names, class names, SQL logic)
- If the two analysts disagree, pick the most technically accurate version
- If one analyst captured a detail the other missed, include it
- Mark uncertain information with ⚠️
- Write [TO BE COMPLETED] for genuinely missing information — never invent data
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
