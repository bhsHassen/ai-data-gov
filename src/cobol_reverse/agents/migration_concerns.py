"""
MigrationConcerns agent — lists the migration pitfalls / attention points:
EBCDIC, COMP-3, dates, REDEFINES, GO TO, 88-levels, IDMS/IMS access, etc.
"""
from __future__ import annotations

from ..llm import build_client, get_model
from ._common import clean_listing, head, ANTI_HALLU


SYSTEM_PROMPT = f"""\
Tu es un expert en migration COBOL mainframe → stack moderne (Java/Spring,
Python, PostgreSQL/Oracle). Mission : produire la liste des POINTS
D'ATTENTION pour la migration, avec pour chacun :
  - le constat factuel (avec ligne)
  - l'impact migration
  - la recommandation technique

{ANTI_HALLU}

THÈMES À COUVRIR (n'invente RIEN, ne mentionne que ce qui est détecté) :

1. **EBCDIC vs UTF-8** — caractères nationaux dans PIC X
2. **Packed decimal (COMP-3)** — stockage compact, à mapper sur DECIMAL/BigDecimal
3. **Dates manuelles** — conversion AAMMJJ ↔ AAAA-MM-JJ codée à la main
4. **REDEFINES** — réutilisation de zone mémoire, à remodéliser
5. **GO TO** — sauts non structurés à transformer en if/else
6. **88-levels** — domaines énumérés, candidats Enum Java
7. **OCCURS DEPENDING ON** — tableaux à taille variable
8. **PERFORM THRU** — blocs de paragraphes, à refactorer en méthodes
9. **CALL externes** — dépendances inter-programmes
10. **Accès DB / Fichiers** — DB2 / IDMS / IMS / VSAM, type de migration
11. **CICS** — transactions, séparation interactive/batch
12. **Variables globales / WORKING-STORAGE partagé** — état global à isoler

FORMAT DE RÉPONSE (Markdown strict) :

## ⚠️ Points d'attention pour la migration

### 1. <Thème>
- **Constat** : <description courte> — N occurrences [lignes X, Y, Z]
- **Impact** : <conséquence concrète pour la migration>
- **Recommandation** : <approche technique à adopter>

(répéter pour chaque thème détecté ; OMETTRE les thèmes non détectés)

## Synthèse risque
| Thème | Sévérité | Volumétrie |
|-------|----------|------------|
| COMP-3 | 🟢 faible | 12 champs |
| REDEFINES | 🟡 modérée | 3 cas |
| GO TO | 🔴 forte | 8 occurrences |

Sévérité : 🟢 faible · 🟡 modérée · 🔴 forte
"""


USER_TEMPLATE = """\
## SOURCE COBOL (nettoyé)
```
{source}
```

## STRUCTURE TARGET (référence pour COMP-3, 88-levels, REDEFINES)
```
{target_desc}
```

## STRUCTURE INPUT
```
{input_desc}
```

Liste tous les points d'attention RÉELLEMENT détectés dans le matériau ci-dessus.
"""


def list_concerns(source: str, target_desc: str, input_desc: str,
                  temperature: float = 0.0) -> str:
    client = build_client()
    model  = get_model("doc")
    src    = head(clean_listing(source), 80_000)

    resp = client.chat.completions.create(
        model       = model,
        temperature = temperature,
        extra_body  = {"enable_thinking": False},
        messages    = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": USER_TEMPLATE.format(
                source      = src,
                target_desc = target_desc or "(non fourni)",
                input_desc  = input_desc  or "(non fourni)",
            )},
        ],
    )
    msg = resp.choices[0].message
    return (msg.content or getattr(msg, "reasoning_content", None) or "").strip()
