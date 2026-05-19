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

The agent is instructed to:
  - Cite line numbers for every rule found
  - Distinguish MOVE (direct) / COMPUTE (calculated) / DEFAULT (INITIALIZE/VALUE)
  - List conditions (IF / EVALUATE / WHEN) that gate each assignment
  - Report validation rules and error-handling found for the field
  - Say explicitly "non trouvé dans le code" if the field has no assignment

The agent must NOT invent assignments — every claim must be traceable.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..llm import build_client, get_model
from ..parsers.copybook import CopyField


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
    """Builds the user prompt for one field."""

    # PIC info for context
    pic_info = f"PIC {field.pic}" if field.pic else "(groupe, pas de PIC)"
    if field.usage:
        pic_info += f" USAGE {field.usage}"
    if field.occurs:
        pic_info += f" OCCURS {field.occurs}"
    if field.values_88:
        pic_info += f"\n   Valeurs 88 : {', '.join(field.values_88[:10])}"

    return f"""\
## Champ à analyser
Nom   : {field.name}
Type  : {pic_info}
Niveau: {field.level}

## Structure des champs INPUT (copybook)
{input_desc}

## Structure des champs TARGET / OUTPUT (copybook)
{target_desc}

## Code COBOL source
```
{source_cobol}
```

## Programme compilé (listing)
```
{compiled}
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
        messages    = [
            {"role": "system",  "content": system},
            {"role": "user",    "content": user},
        ],
    )

    md = resp.choices[0].message.content.strip()
    found = "non trouvé" not in md.lower() and "aucune alimentation" not in md.lower()

    return FieldSpec(field_name=field.name, markdown=md, found=found)
