"""
PseudoCoder agent — produces a readable pseudo-code of the program flow,
extracted from the PROCEDURE DIVISION. Used as the algorithmic skeleton
for the Java/Spring Batch rewrite.
"""
from __future__ import annotations

from ..llm import build_client, get_model
from ._common import clean_listing, extract_block, head, ANTI_HALLU


SYSTEM_PROMPT = f"""\
Tu es un expert en analyse de flot de contrôle COBOL. Mission : produire un
PSEUDO-CODE lisible par un développeur Java/Python qui ne connaît pas le COBOL,
décrivant l'algorithme global du programme.

{ANTI_HALLU}

CONVENTIONS DE PSEUDO-CODE :
- Indentation par 4 espaces.
- Mots-clés en MAJUSCULES : POUR, TANT QUE, SI, SINON, FIN SI, APPELER, LIRE, ÉCRIRE, RETOURNER.
- Boucles COBOL :
    PERFORM <P> UNTIL <cond>          → TANT QUE NON (<cond>) FAIRE … FIN TQ
    PERFORM <P> VARYING I FROM 1…     → POUR I de 1 à N FAIRE … FIN POUR
    PERFORM <P> <N> TIMES             → RÉPÉTER N FOIS … FIN RÉPÉTER
- IF/ELSE/EVALUATE → SI / SINON / SELON / CAS
- MOVE A TO B → b ← a
- COMPUTE → expression mathématique
- READ → LIRE depuis <fichier>
- WRITE → ÉCRIRE dans <fichier>
- CALL "X" USING … → APPELER X(args)
- GO TO → ALLER À (à noter comme code-smell)

FORMAT DE RÉPONSE (Markdown strict) :

## Algorithme global (pseudo-code)

```text
PROGRAMME <NOM>
    -- Phase 1 : initialisation [paragraphe NOM, lignes 100-180]
    INITIALISER les zones de travail
    ...

    -- Phase 2 : boucle principale [paragraphe NOM, lignes 200-280]
    TANT QUE NON fin-de-fichier FAIRE
        LIRE enregistrement
        ...
    FIN TANT QUE

    -- Phase 3 : clôture [paragraphe NOM, lignes 530-560]
    ...

    RETOURNER code-retour
FIN PROGRAMME
```

## Sous-routines / paragraphes internes
Pour chaque paragraphe non trivial appelé par PERFORM, produire un mini-bloc :

### <NOM-PARAGRAPHE>  [lignes A-B]
```text
<pseudo-code détaillé du paragraphe>
```

## Complexité observée
- **Nombre de paragraphes** : N
- **GO TO** : nombre + lignes (à supprimer en migration)
- **PERFORM imbriqués** : oui / non
- **EVALUATE** : nombre de branches
- **CALL externes** : liste
"""


USER_TEMPLATE = """\
## PROCEDURE DIVISION (extraite et nettoyée)
```
{procedure}
```

## DATA DIVISION (en référence pour les noms de variables)
```
{data_div}
```

Produis le pseudo-code complet selon le format strict.
"""


def generate_pseudo_code(source: str, temperature: float = 0.0) -> str:
    client = build_client()
    model  = get_model("doc")

    cleaned   = clean_listing(source)
    procedure = extract_block(cleaned, "PROCEDURE DIVISION", end_kw=None, max_chars=80_000)
    data_div  = extract_block(cleaned, "DATA DIVISION", "PROCEDURE DIVISION", max_chars=20_000)

    if not procedure:
        procedure = head(cleaned, 80_000)  # fallback if no marker

    resp = client.chat.completions.create(
        model       = model,
        temperature = temperature,
        extra_body  = {"enable_thinking": False},
        messages    = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": USER_TEMPLATE.format(
                procedure = procedure,
                data_div  = data_div or "(non détectée)",
            )},
        ],
    )
    msg = resp.choices[0].message
    return (msg.content or getattr(msg, "reasoning_content", None) or "").strip()
