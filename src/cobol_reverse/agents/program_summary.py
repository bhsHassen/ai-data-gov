"""
ProgramSummary agent — produces the global preamble of the migration spec:
role, inputs, outputs, external calls, paragraph structure.
"""
from __future__ import annotations

from ..llm import build_client, get_model
from ._common import clean_listing, head, ANTI_HALLU


SYSTEM_PROMPT = f"""\
Tu es un expert en rétro-ingénierie COBOL mainframe.
Mission : produire le PRÉAMBULE d'un dossier de migration vers Java/SQL.
L'audience NE connaît PAS le COBOL. Sois clair, factuel, en langage métier.

{ANTI_HALLU}

FORMAT DE RÉPONSE (Markdown, ordre strict) :

## 1. Rôle du programme
<2-4 phrases en langage métier — à quoi sert ce module dans la chaîne SI>

## 2. Entrées (sources de données)
- **<type>** : <nom du fichier / table / DB> — <description>  [ligne X]
  *(types possibles : Fichier séquentiel, Table DB2, Table IDMS, VSAM, IMS, paramètres CICS)*

## 3. Sorties (destinations)
- **<type>** : <nom> — <description>  [ligne X]
- **Code retour** : <valeurs émises par RETURN-CODE / GOBACK> si détectées

## 4. Appels externes (CALL "...")
| Programme appelé | Type | Rôle métier | Ligne |
|------------------|------|-------------|-------|
| MODCALC          | CALL | calcul TVA  | 230   |

Si aucun CALL → écrire « Aucun appel externe détecté. »

## 5. Structure des paragraphes
Liste des paragraphes principaux (sections, paragraphes PERFORM'd) :
| Paragraphe | Rôle | Lignes |
|------------|------|--------|
| INIT       | Initialisations | 100-180 |

## 6. Fichiers / DB déclarés (FD, SELECT, EXEC SQL DECLARE)
- <résumé court avec lignes>
"""


USER_TEMPLATE = """\
## SOURCE COBOL (nettoyé du code objet)
```
{source}
```

## LISTING COMPILÉ (nettoyé) — extrait
```
{compiled}
```

Produis le préambule selon le format strict. Limite-toi aux éléments
EFFECTIVEMENT présents dans le code ci-dessus.
"""


def summarize_program(source: str, compiled: str,
                      temperature: float = 0.0) -> str:
    """Return the Markdown preamble section."""
    client = build_client()
    model  = get_model("doc")
    src    = head(clean_listing(source),   60_000)
    cmp    = head(clean_listing(compiled), 30_000) if compiled else "(non fourni)"

    resp = client.chat.completions.create(
        model       = model,
        temperature = temperature,
        extra_body  = {"enable_thinking": False},
        messages    = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": USER_TEMPLATE.format(source=src, compiled=cmp)},
        ],
    )
    msg = resp.choices[0].message
    return (msg.content or getattr(msg, "reasoning_content", None) or "").strip()
