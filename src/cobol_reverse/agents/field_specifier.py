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
Tu es un expert en rétro-ingénierie COBOL mainframe (IBM z/OS).
Ta mission : produire la spécification d'alimentation d'UN champ cible précis, \
à partir du code fourni — et UNIQUEMENT à partir de ce code.

═══════════════════════════════════════════════════════
RÈGLES ANTI-HALLUCINATION — ABSOLUES ET NON NÉGOCIABLES
═══════════════════════════════════════════════════════

R1. ZÉRO INVENTION
    Chaque affirmation doit être directement vérifiable dans le code fourni.
    Si une information n'est PAS dans le code → ne l'écris pas.
    Ne complète jamais un raisonnement par une supposition, même plausible.

R2. CITATIONS OBLIGATOIRES
    Toute règle d'alimentation doit citer le numéro de ligne exact entre crochets.
    Format : [ligne 245] ou [lignes 245-248].
    Numéro de ligne = le numéro affiché en début de ligne dans le listing fourni.
    Si tu ne peux pas citer un numéro de ligne précis, tu NE PEUX PAS énoncer la règle.

R3. DISTINCTION DES TYPES D'ALIMENTATION
    Indique explicitement pour chaque règle :
    - MOVE direct       → valeur copiée d'un champ source ou d'une constante littérale
    - COMPUTE / calcul  → résultat d'une opération arithmétique (formule complète)
    - INITIALIZE/VALUE  → valeur par défaut à l'initialisation
    - REDEFINES         → le champ réutilise une zone mémoire d'un autre champ

R4. CONDITIONS OBLIGATOIRES
    Pour chaque règle, précise la condition qui la déclenche :
    - "Toujours" si exécutée sans condition
    - "Si [condition exacte du IF]" avec la condition copiée verbatim du code
    - "Cas EVALUATE WHEN [valeur]" pour les branches EVALUATE
    - "Lors du traitement [NOM-PARAGRAPHE]" si la règle est dans un paragraphe spécifique

R5. NON-TROUVÉ EXPLICITE — MUTUELLEMENT EXCLUSIF AVEC LES RÈGLES
    DEUX CAS SEULEMENT, jamais les deux ensemble :
    CAS A — Des règles ont été trouvées → remplis le tableau, section Contrôles, Remarques.
             NE PAS écrire "Non trouvé" ni le bloc ⚠️.
    CAS B — Aucune règle trouvée → écrire UNIQUEMENT :
             "⚠️ Non trouvé dans le code source — aucune alimentation détectée."
             NE PAS inventer de règles, NE PAS écrire de tableau vide.
    Si tu as rempli au moins une ligne du tableau → tu es dans le CAS A.
    N'écris JAMAIS une règle hypothétique du type "pourrait être alimenté par...".

R6. CHAMPS SOURCE EXACTS
    Quand un MOVE provient d'un champ source, cite le nom exact du champ source
    tel qu'il apparaît dans le code (respect de la casse COBOL).

R7. CONTRÔLES ET VALIDATIONS
    Liste uniquement les contrôles explicitement codés sur CE champ (IF, EVALUATE,
    88-level conditions, INSPECT). Ne déduis pas des contrôles implicites.

R8. LANGUE FRANÇAISE
    Toute la réponse est en français. Les noms de champs COBOL restent en majuscules.

═══════════════════════════════════════════════════════
FORMAT DE RÉPONSE STRICT (Markdown)
═══════════════════════════════════════════════════════

### {field_name}

**Nom technique** : {field_name}
**Libellé métier**: <libellé extrait du commentaire dans le code, ou "(non trouvé dans les commentaires)">
**Type**          : <PIC clause exacte>
**Description**   : <une phrase décrivant le rôle fonctionnel du champ — basée sur le libellé et le contexte du code>

**Alimentation**
| # | Type | Condition | Règle | Ligne(s) |
|---|------|-----------|-------|---------|
| 1 | MOVE / COMPUTE / INIT | Toujours / Si ... | Description exacte | [ligne X] |

> Si aucune alimentation trouvée :
> ⚠️ **Non trouvé dans le code source — aucune alimentation détectée.**

**Contrôles**
- <contrôle 1 avec référence de ligne>
- Aucun contrôle détecté. ← si rien trouvé

**Remarques**
- <uniquement si une information structurelle importante est visible dans le code>
- Laisser vide si rien à signaler.
"""


def _build_prompt(
    field:        CopyField,
    source_cobol: str,
    compiled:     str,
    input_desc:   str,
    target_desc:  str,
) -> str:
    """Builds the user prompt for one field — with focused context."""

    # PIC info
    pic_info = f"PIC {field.pic}" if field.pic else "(groupe, pas de PIC)"
    if field.usage:
        pic_info += f" USAGE {field.usage}"
    if field.occurs:
        pic_info += f" OCCURS {field.occurs}"
    if field.values_88:
        pic_info += f"\n   Valeurs 88 : {', '.join(field.values_88[:10])}"

    libelle = field.description or "(non trouvé dans les commentaires)"

    # Focused source extract
    source_snippet, src_hits = _extract_context(source_cobol, field.name)
    data_div  = _data_division_header(source_cobol)
    src_note  = (f"({src_hits} occurrence(s) trouvée(s) — fenêtre ±{CONTEXT_LINES} lignes)"
                 if src_hits else "(aucune occurrence directe — source complet tronqué)")

    # Focused compiled extract (lighter)
    compiled_snippet, cmp_hits = _extract_context(
        compiled, field.name,
        context=20,
        max_chars=MAX_COMPILED_CHARS,
    ) if compiled else ("(absent)", 0)
    cmp_note = f"({cmp_hits} occurrence(s))" if compiled else "(non fourni)"

    data_section = (
        f"\n## En-tête DATA DIVISION (définitions de zones)\n```\n{data_div}\n```\n"
        if data_div else ""
    )

    occurrences_warning = "" if src_hits else (
        "\n⚠️ ATTENTION : aucune occurrence directe du nom de champ trouvée dans "
        "les extraits ci-dessous. Si tu ne trouves pas de règle d'alimentation "
        "explicite, applique la règle R5 : écris 'Non trouvé dans le code source'.\n"
    )

    return f"""\
## CHAMP À SPÉCIFIER
┌─────────────────────────────────────────────────────────────┐
│ Nom technique : {field.name:<44} │
│ Libellé métier: {libelle:<44} │
│ Type (PIC)    : {pic_info:<44} │
│ Niveau COBOL  : {field.level:<44} │
└─────────────────────────────────────────────────────────────┘

RAPPEL CRITIQUE : tu ne dois écrire QUE ce qui est littéralement présent
dans le code ci-dessous. Chaque règle doit avoir un [numéro de ligne].
{occurrences_warning}
## Structure INPUT — champs en entrée du programme (copybook)
```
{input_desc}
```

## Structure TARGET — champs en sortie / cible (copybook)
```
{target_desc}
```
{data_section}
## Extraits du source COBOL {src_note}
(Numéros de ligne affichés à gauche — utilise-les pour tes citations)
```
{source_snippet}
```

## Extraits du listing compilé {cmp_note}
```
{compiled_snippet}
```

---
Produis maintenant la spécification d'alimentation du champ **{field.name}** \
({libelle}) en respectant STRICTEMENT le format et les règles R1 à R8.
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
