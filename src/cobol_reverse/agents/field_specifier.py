"""
FieldSpecifier agent — for ONE target field, analyses the COBOL source and
compiled listing to produce a structured specification.

Output per field (Markdown):
    ### NOM-DU-CHAMP
    **Nom**          : NOM-DU-CHAMP
    **Description**  : <sens métier>
    **Alimentation** :
    - Règle 1 : ...   [ligne X]
    - Règle 2 : ...   [ligne Y]
    - Contrôles : ...

Context strategy (token budget):
    - Copybooks are sent in full (small).
    - COBOL source  : only lines that mention the field name ± CONTEXT_LINES,
      plus the DATA DIVISION header block (first lines until PROCEDURE DIVISION).
    - Compiled listing: same extraction, hard-capped at MAX_COMPILED_CHARS.
    - If no occurrence is found the full source is sent but truncated to
      MAX_SOURCE_CHARS.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..llm import build_client, get_model
from ..parsers.copybook import CopyField


# ─────────────────────────────────────────────────────────────────────────────
#  Tuneable limits
# ─────────────────────────────────────────────────────────────────────────────

CONTEXT_LINES      = 40    # lines before/after each occurrence in source
MAX_SOURCE_CHARS   = 80_000   # hard cap on source snippet sent to LLM
MAX_COMPILED_CHARS = 40_000   # hard cap on compiled listing sent


# ─────────────────────────────────────────────────────────────────────────────
#  Context extraction helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_context(text: str, field_name: str,
                     context: int = CONTEXT_LINES,
                     max_chars: int = MAX_SOURCE_CHARS) -> tuple[str, int]:
    """
    Return (snippet, occurrence_count).

    Finds every line that contains `field_name` (word-boundary match),
    collects a window of `context` lines around each hit, deduplicates,
    and joins with a separator.  If no hit is found, returns the original
    text truncated to max_chars.
    """
    lines = text.splitlines()
    n     = len(lines)
    pat   = re.compile(r"\b" + re.escape(field_name) + r"\b", re.IGNORECASE)

    hit_indices: list[int] = [i for i, ln in enumerate(lines) if pat.search(ln)]

    if not hit_indices:
        truncated = text[:max_chars]
        if len(text) > max_chars:
            truncated += f"\n[... truncated — {len(lines)} lines total ...]"
        return truncated, 0

    # Merge overlapping windows
    selected: list[bool] = [False] * n
    for idx in hit_indices:
        lo = max(0, idx - context)
        hi = min(n, idx + context + 1)
        for j in range(lo, hi):
            selected[j] = True

    # Build snippet with line numbers and gap markers
    parts: list[str] = []
    in_gap = False
    for i, (keep, line) in enumerate(zip(selected, lines), 1):
        if keep:
            in_gap = False
            parts.append(f"{i:>6}  {line}")
        else:
            if not in_gap:
                parts.append("       [...]")
                in_gap = True

    snippet = "\n".join(parts)
    if len(snippet) > max_chars:
        snippet = snippet[:max_chars] + "\n[... truncated ...]"
    return snippet, len(hit_indices)


def _data_division_header(source: str, max_lines: int = 80) -> str:
    """
    Extract the DATA DIVISION block (up to PROCEDURE DIVISION or max_lines).
    Gives the LLM field definitions even when the field itself has no MOVE.
    """
    lines  = source.splitlines()
    start  = None
    end    = len(lines)
    for i, ln in enumerate(lines):
        uln = ln.upper()
        if start is None and "DATA DIVISION" in uln:
            start = i
        elif start is not None and "PROCEDURE DIVISION" in uln:
            end = i
            break
    if start is None:
        return ""
    block = lines[start: min(end, start + max_lines)]
    return "\n".join(block)


# ─────────────────────────────────────────────────────────────────────────────
#  Prompts
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
Tu es un expert en analyse de code COBOL mainframe.
Ta mission : produire la spécification d'alimentation d'UN champ cible précis.

RÈGLES ABSOLUES :
1. Chaque règle que tu énonces DOIT citer le numéro de ligne du code source (ex: [ligne 245]).
2. Tu distingues :
   - MOVE direct      : la valeur vient directement d'un champ source ou d'une constante
   - COMPUTE / calcul : la valeur est calculée (formule, arithmétique)
   - INITIALIZE / VALUE : valeur par défaut ou initialisation
3. Pour chaque alimentation, tu précises la CONDITION qui la déclenche
   (IF, EVALUATE WHEN, toujours, uniquement si...).
4. Tu listes les contrôles et validations effectués sur ce champ.
5. Si le champ n'est PAS alimenté dans le code, tu écris explicitement :
   "Non trouvé dans le code source — aucune alimentation détectée."
6. Tu n'inventes RIEN. Tout ce que tu écris doit être dans le code fourni.
7. Tu réponds en FRANÇAIS.
8. Format de réponse STRICT (Markdown) :

### {field_name}

**Nom**          : {field_name}
**Description**  : <une phrase décrivant ce que représente ce champ>

**Alimentation**
- Règle 1 : <description> [ligne X]
- Règle 2 : <description> [ligne Y]
- Contrôles : <validations, conditions de rejet, codes erreur>

(si aucune règle : écrire "Non trouvé dans le code source.")
"""


def _build_prompt(
    field:        CopyField,
    source_cobol: str,
    compiled:     str,
    input_desc:   str,
    target_desc:  str,
) -> str:
    """Builds the user prompt for one field — with focused context."""

    # PIC info for context
    pic_info = f"PIC {field.pic}" if field.pic else "(groupe, pas de PIC)"
    if field.usage:
        pic_info += f" USAGE {field.usage}"
    if field.occurs:
        pic_info += f" OCCURS {field.occurs}"
    if field.values_88:
        pic_info += f"\n   Valeurs 88 : {', '.join(field.values_88[:10])}"

    # Focused source extract
    source_snippet, src_hits = _extract_context(source_cobol, field.name)
    data_div = _data_division_header(source_cobol)
    src_note = (f"({src_hits} occurrence(s) trouvée(s) — fenêtre ±{CONTEXT_LINES} lignes)"
                if src_hits else "(aucune occurrence directe — source complet tronqué)")

    # Focused compiled extract (lighter)
    compiled_snippet, cmp_hits = _extract_context(
        compiled, field.name,
        context=20,
        max_chars=MAX_COMPILED_CHARS,
    ) if compiled else ("(absent)", 0)
    cmp_note = f"({cmp_hits} occurrence(s))" if compiled else "(non fourni)"

    data_section = (
        f"\n## En-tête DATA DIVISION (définitions)\n```\n{data_div}\n```\n"
        if data_div else ""
    )

    return f"""\
## Champ à analyser
Nom   : {field.name}
Type  : {pic_info}
Niveau: {field.level}

## Structure INPUT (copybook)
{input_desc}

## Structure TARGET / OUTPUT (copybook)
{target_desc}
{data_section}
## Extraits COBOL source {src_note}
```
{source_snippet}
```

## Extraits listing compilé {cmp_note}
```
{compiled_snippet}
```

---
Produis maintenant la spécification d'alimentation du champ **{field.name}** \
en respectant le format et les règles du system prompt.
"""


# ─────────────────────────────────────────────────────────────────────────────
#  Agent function
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FieldSpec:
    field_name: str
    markdown:   str
    found:      bool       # False if "non trouvé" in output


def specify_field(
    field:        CopyField,
    source_cobol: str,
    compiled:     str,
    input_desc:   str,
    target_desc:  str,
    temperature:  float = 0.0,
) -> FieldSpec:
    """
    Calls the LLM to produce the alimentation spec for one target field.
    Returns a FieldSpec with the raw Markdown.
    """
    client = build_client()
    model  = get_model("doc")

    system = SYSTEM_PROMPT.replace("{field_name}", field.name)
    user   = _build_prompt(field, source_cobol, compiled, input_desc, target_desc)

    resp = client.chat.completions.create(
        model       = model,
        temperature = temperature,
        extra_body  = {"enable_thinking": False},
        messages    = [
            {"role": "system",  "content": system},
            {"role": "user",    "content": user},
        ],
    )

    msg = resp.choices[0].message
    md  = (msg.content or getattr(msg, "reasoning_content", None) or "").strip()
    found = bool(md) and "non trouvé" not in md.lower() and "aucune alimentation" not in md.lower()

    return FieldSpec(field_name=field.name, markdown=md, found=found)
