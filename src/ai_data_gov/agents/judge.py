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
1. The original source artifacts (source code, DDL, existing documentation, tests)
2. Two independent specifications written by two analysts from those artifacts

## YOUR TASK
Produce a single SUPERIOR specification by:
- Verifying each analyst's claims against the original source artifacts
- Taking the most accurate and complete information from both drafts
- Correcting errors or gaps that both analysts missed — using the source artifacts as ground truth

## VERIFICATION RULES
- **Ground truth first**: always verify against the source artifacts, not just between the two drafts.
- **Precision wins**: prefer the most specific version (exact field names, table names, business rules). Preserve the original casing of field names (`UPPER_SNAKE_CASE` for DDL columns, `camelCase` for Java bean fields).
- **Coverage wins**: if one analyst captured something the other missed, include it.
- **Correction**: if both analysts are wrong or incomplete on a point, fix it using the source artifacts.
- **Legacy code is sufficient**: the Java/XML artifacts (`*Bean.java`, `*ImportWork.java`, `*ImportWorkTest.java`, `*.xml`, `FixedLengthTokenizer`, mapping configs) contain enough information to reconstitute the full mapping. Before accepting `[INFORMATION NOT FOUND]` from either draft, re-read the legacy code — offsets, lengths, field order, transformation rules, and quality checks are almost always derivable.
- **Test classes are ground truth for behaviour**: `*ImportWorkTest.java` gives concrete input/output examples. Use them to confirm or correct Section 3 (Transformation) and Section 6 (Quality).
- **No invention**: for Section 1 attributes (Owner, Criticality, Frequency, Data domain, Downstream consumers), if the information is not explicit in the artifacts, force `[INFORMATION NOT FOUND — source required]`. Never let either analyst slip a guess through.
- **Honest gaps**: only if the information is genuinely absent from ALL artifacts (DDL, Java, XML, docs, tests), write `[INFORMATION NOT FOUND — source required]`.

## FORMAT RULES
- Output language: **English**, even if source artifacts are in another language.
- Each section: 2-3 plain-language sentences + precise technical table.
- **Confidence column on Section 3 only** — use pictograms: 🟢 HIGH / 🟡 MEDIUM / 🔴 LOW. Section 2 has no Confidence column.
- Every 🟡 MEDIUM or 🔴 LOW entry MUST be followed by a one-line explanation stating why confidence is not HIGH and what evidence would upgrade it.
- **Section 2 — row order**: fields MUST appear in source declaration order (DDL / XML / ImportWork parsing order). Reorder if either draft has them wrong.
- **Section 2 — offsets** (file flows): the FIRST offset MUST be `0`. Each subsequent offset = previous offset + previous length. Recompute from scratch; do not trust either draft blindly. For non-file flows put `N/A`.
- **Section 2 — columns**: `Source | Field | Type | Length | Offset | PK | Nullable | Default | Business Meaning`.
- **Section 4 — columns**: `Target Table | Field | Type | Nullable | Constraint | Populated From`. Do not repeat Section 3 rules in the "Populated From" column — reference them.
- **Section 5 — macroscopic only**: business-impact and system-level lineage, NOT a field-level repeat of Section 3. Columns: `Upstream System | This Flow | Downstream System / Usage | Business Impact`.
- **Section 6 — columns**: `Check | Fields Concerned | Severity | Action if Failed`. Severity: 🔴 Blocking / 🟠 Warning / 🔵 Info.
- **Section 7**: Spring Batch is a **reference framework** — guidelines and attention points only (Reader / Processor / Writer). No source code.
- No redundancy — do not repeat information already stated in a previous section.
- Confluence-ready Markdown: tables, **bold**, bullet points — no HTML, no raw code.

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
        location:       Optional physical region (e.g. Sydney, London, Paris).

    Returns:
        Final verified and synthesized spec as Markdown string.
    """
    loc_note = f" — Location: {location} (physical region)" if location else ""

    user_content = f"""Flow: {flow_name}{loc_note}

=== SOURCE ARTIFACTS (ground truth) ===
{raw_context}

=== ANALYST 1 DRAFT (Qwen3) ===
{draft_analyst1}

=== ANALYST 2 DRAFT (Codestral) ===
{draft_analyst2}

Verify both drafts against the source artifacts and produce the superior final specification in English.
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
