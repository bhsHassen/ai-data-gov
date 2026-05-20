"""
FlowTracer agent — produces the input→target cartography:
for each TARGET field, the chain of operations that fed it from INPUT fields.
Uses the already-generated FIELD_SPEC.md as primary trusted input.
"""
from __future__ import annotations

from ..llm import build_client, get_model
from ._common import clean_listing, head, ANTI_HALLU


SYSTEM_PROMPT = f"""\
Tu es un expert en data lineage COBOL. Mission : tracer le flux INPUT → TARGET
de TOUS les champs cibles du programme, sous forme de cartographie tabulaire.

{ANTI_HALLU}

Tu reçois la spécification champ-par-champ déjà produite (FIELD_SPEC). Elle
contient pour chaque champ cible : les règles d'alimentation, les conditions
et les numéros de ligne — c'est la SOURCE DE VÉRITÉ. Ne contredis pas le
FIELD_SPEC : reformule-le en cartographie globale.

FORMAT DE RÉPONSE (Markdown strict) :

## Cartographie INPUT → TARGET

Table principale — UNE ligne par champ TARGET :

| Champ TARGET | Libellé métier | Source(s) INPUT / constante | Transformation | Lignes |
|--------------|----------------|------------------------------|-----------------|--------|
| F1788 | CODE TYPE USAGE | IN-CODE-USAGE | MOVE direct | 245 |
| MNT-TTC | MONTANT TTC | IN-MONTANT + WS-TVA | calcul × taux | 401-405 |
| OUT-DATE | DATE EDITION | IN-DATE-AAMMJJ | reformatage AAMMJJ → AAAA-MM-JJ | 520-528 |
| OUT-FILLER | (réservé) | — | constante VALUE SPACES | 80 |
| OUT-UNKNOWN | ? | (non tracé) | — | — |

Règles :
- « Source(s) INPUT » : nom du/des champ(s) INPUT qui alimente(nt) ; si plusieurs, séparer par '+'
- « Constante » : VALUE / ZERO / SPACES / littéral
- « (non tracé) » si le FIELD_SPEC indique « Non trouvé »
- « Transformation » : court (MOVE direct, calcul, reformatage, concaténation, table de lookup…)
- Conserver l'ordre d'apparition des champs dans le TARGET copybook

À la fin, ajouter une synthèse :

## Synthèse du flux
- **Total champs cible** : N
- **Champs tracés** : M (X %)
- **Champs alimentés par constante** : K
- **Champs non tracés** : N-M
- **Champs INPUT effectivement utilisés** : liste (avec lignes où ils apparaissent)
- **Champs INPUT non utilisés** : liste (potentiellement supprimables en migration)
"""


USER_TEMPLATE = """\
## STRUCTURE INPUT
```
{input_desc}
```

## STRUCTURE TARGET
```
{target_desc}
```

## SPÉCIFICATION CHAMP-PAR-CHAMP (source de vérité)
{field_spec}

## CODE SOURCE COBOL (référence, lignes citables)
```
{source}
```

Produis la cartographie complète selon le format strict.
"""


def trace_flow(input_desc: str, target_desc: str, field_spec: str,
               source: str, temperature: float = 0.0) -> str:
    client = build_client()
    model  = get_model("doc")
    src    = head(clean_listing(source), 40_000)
    spec   = head(field_spec, 80_000)

    resp = client.chat.completions.create(
        model       = model,
        temperature = temperature,
        extra_body  = {"enable_thinking": False},
        messages    = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": USER_TEMPLATE.format(
                input_desc  = input_desc  or "(non fourni)",
                target_desc = target_desc or "(non fourni)",
                field_spec  = spec,
                source      = src,
            )},
        ],
    )
    msg = resp.choices[0].message
    return (msg.content or getattr(msg, "reasoning_content", None) or "").strip()
