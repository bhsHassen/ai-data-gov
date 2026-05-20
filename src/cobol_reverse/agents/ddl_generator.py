"""
DdlGenerator agent — produces a CREATE TABLE DDL for the target structure,
with type translations (R9.1), CHECK constraints from 88-levels, and
COMMENT ON COLUMN carrying the business label.
"""
from __future__ import annotations

from ..llm import build_client, get_model
from ._common import ANTI_HALLU


SYSTEM_PROMPT = f"""\
Tu es un expert en modélisation de bases de données relationnelles, spécialisé
dans la migration COBOL → SQL (PostgreSQL / Oracle / DB2 LUW).
Mission : produire un script DDL CREATE TABLE pour la structure TARGET.

{ANTI_HALLU}

RÈGLES DE TRADUCTION DES TYPES :
  PIC X(n)              → VARCHAR(n)
  PIC X                 → CHAR(1)
  PIC 9(n)              → INTEGER si n ≤ 9, BIGINT si n ≤ 18, NUMERIC(n) sinon
  PIC 9(n)V9(m)         → NUMERIC(n+m, m)
  PIC S9(n)             → INTEGER signé
  PIC S9(n)V9(m) COMP-3 → NUMERIC(n+m, m)        -- packed decimal
  PIC 9(n) COMP / BINARY→ INTEGER / BIGINT       -- entier binaire
  PIC 9(8) (date)       → DATE                   -- si nom évoque date

RÈGLES DE NOMMAGE :
- Convertir les noms COBOL en SQL : 'F1788-CODE' → 'F1788_CODE' (tirets → underscores)
- Le nom de table = nom du copybook target SANS extension, en MAJUSCULES
- Indiquer en commentaire SQL le nom COBOL d'origine

CONTRAINTES :
- Toute valeur 88-level → contrainte CHECK (col IN ('val1','val2',…))
- NOT NULL UNIQUEMENT si le champ a une VALUE clause ou un usage évident
  (clé, identifiant). Sinon laisser nullable et le mentionner en commentaire.
- COMMENT ON COLUMN avec le libellé métier extrait du FIELD_SPEC

FORMAT DE RÉPONSE (Markdown) :

## Modèle de données cible (DDL)

```sql
-- Généré à partir de target.cpy et FIELD_SPEC.md
-- Cible : PostgreSQL 14+ (adaptable Oracle / DB2 par renommage de types)

CREATE TABLE T_<NOM_TABLE> (
    F1788_CODE      CHAR(2)       NOT NULL,        -- COBOL: F1788 PIC X(02)
    MNT_TTC         NUMERIC(10,2),                 -- COBOL: MNT-TTC PIC 9(8)V99 COMP-3
    OUT_DATE        DATE,                          -- COBOL: OUT-DATE PIC 9(08)
    CODE_STATUT     CHAR(2)
        CHECK (CODE_STATUT IN ('01','02','03','99')),  -- COBOL 88-levels
    ...
);

-- Libellés métier (COMMENT ON COLUMN)
COMMENT ON COLUMN T_<NOM>.F1788_CODE   IS 'CODE TYPE USAGE CONTREPARTIE';
COMMENT ON COLUMN T_<NOM>.MNT_TTC      IS 'MONTANT TTC';
...
```

## Notes de migration sur le modèle
- Liste des choix de typage ambigus ou notables (ex : « PIC 9(8) interprété
  comme DATE car le libellé contient le mot DATE — à valider avec le métier »).
- Champs FILLER ignorés (rappel : ce sont des zones de remplissage).
- REDEFINES non implémentés : la deuxième vue est ignorée, à remodéliser
  côté applicatif si nécessaire.
"""


USER_TEMPLATE = """\
## STRUCTURE TARGET (copybook brut)
```
{target_desc}
```

## SPÉCIFICATION CHAMP-PAR-CHAMP (libellés métier à reprendre dans COMMENT ON COLUMN)
{field_spec}

Nom suggéré pour la table : T_{project_upper}

Produis le DDL complet.
"""


def generate_ddl(target_desc: str, field_spec: str, project_name: str,
                 temperature: float = 0.0) -> str:
    client = build_client()
    model  = get_model("doc")

    resp = client.chat.completions.create(
        model       = model,
        temperature = temperature,
        extra_body  = {"enable_thinking": False},
        messages    = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": USER_TEMPLATE.format(
                target_desc   = target_desc or "(non fourni)",
                field_spec    = field_spec[:80_000],
                project_upper = project_name.upper().replace("-","_"),
            )},
        ],
    )
    msg = resp.choices[0].message
    return (msg.content or getattr(msg, "reasoning_content", None) or "").strip()
