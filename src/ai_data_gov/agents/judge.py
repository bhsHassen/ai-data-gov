"""
Judge agent — verifies and synthesizes the best spec from multiple analyst drafts.

Receives the original source artifacts (raw_context) AND both analyst drafts.
This allows the Judge to verify accuracy against the ground truth, not just
compare two opinions.
"""
from __future__ import annotations

from src.ai_data_gov.llm import build_client, get_model


JUDGE_PROMPT = """You are a senior data governance expert and technical reviewer at a global investment bank.

You have received:
1. The original source artifacts (source code, DDL, existing documentation)
2. Two independent specifications written by two analysts from those artifacts

## YOUR TASK
Produce a single SUPERIOR specification by:
- Verifying each analyst's claims against the original source artifacts
- Taking the most accurate and complete information from both drafts
- Correcting errors or gaps that both analysts missed — using the source artifacts as ground truth

## VERIFICATION RULES
- **Ground truth first**: always verify against the source artifacts, not just between the two drafts
- **Precision wins**: prefer the most specific version (exact field names, table names, business rules)
- **Coverage wins**: if one analyst captured something the other missed, include it
- **Correction**: if both analysts are wrong or incomplete on a point, fix it using the source artifacts
- **Honest gaps**: if the information is genuinely absent from all artifacts, write `[INFORMATION NOT FOUND — source required]`

## FORMAT RULES
- Each section: 2-3 plain-language sentences + precise technical table
- Confidence on Sections 2 and 3 only — use pictograms: 🟢 HIGH / 🟡 MEDIUM / 🔴 LOW
- Length and Offset in Section 2 only (from DDL)
- Section 4 Target: simple table (Field, Populated From) — no Length/Offset/Confidence
- Section 5 Lineage: narrative + table without Confidence column
- Section 6 Quality: table without Confidence column
- Section 7: Reader/Processor/Writer guidelines — no source code
- No redundancy — do not repeat information already stated in a previous section
- Confluence-ready Markdown: tables, **bold**, bullet points — no HTML, no raw code

## OUTPUT
Produce all 7 sections in order. The result must be publishable on Confluence as-is.
"""


def judge(
    flow_name: str,
    raw_context: str,
    draft_analyst1: str,
    draft_analyst2: str,
    location: str | None = None,
) -> str:
    """
    Verifies and synthesizes the best spec from two analyst drafts.

    Args:
        flow_name:      Name of the flow.
        raw_context:    Original source artifacts from the Collector (ground truth).
        draft_analyst1: Spec from Analyst 1 (Qwen3).
        draft_analyst2: Spec from Analyst 2 (Codestral).
        location:       Optional location context.

    Returns:
        Final verified and synthesized spec as Markdown string.
    """
    loc_note = f" — Location: {location}" if location else ""

    user_content = f"""Flow: {flow_name}{loc_note}

=== SOURCE ARTIFACTS (ground truth) ===
{raw_context}

=== ANALYST 1 DRAFT (Qwen3) ===
{draft_analyst1}

=== ANALYST 2 DRAFT (Codestral) ===
{draft_analyst2}

Verify both drafts against the source artifacts and produce the superior final specification.
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
