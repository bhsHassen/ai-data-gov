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

CONTEXT_LINES      = 40       # lines before/after each occurrence
MAX_SOURCE_CHARS   = 80_000   # hard cap on source snippet sent to LLM
MAX_COMPILED_CHARS = 40_000   # hard cap on compiled listing sent
MAX_DATA_DIV_CHARS = 40_000   # hard cap on DATA DIVISION extract
PARENT_CONTEXT     = 25       # smaller window for parent-group occurrences


# ─────────────────────────────────────────────────────────────────────────────
#  Listing pre-filter (strip object code + page headers)
# ─────────────────────────────────────────────────────────────────────────────

_RE_OBJ_HEX     = re.compile(r"^\s*[0-9A-F]{6,8}\s+[0-9A-F]{2,}\s")  # object code in margin
_RE_PAGE_HDR    = re.compile(r"^\s*(PP\s+\d|IEL|IGY|IDM|IGYCRP|LineID|PAGE\s+\d)", re.I)
_RE_DATE_HDR    = re.compile(r"^\s*\d{2}[/.-]\d{2}[/.-]\d{2,4}\s+\d{2}[:.]\d{2}")
_RE_FORMFEED    = re.compile(r"[\f\x0c]")


def _clean_listing(text: str) -> str:
    """
    Remove obvious noise from a compiled listing:
    - Pure object-code rows (hex columns in the margin)
    - IBM/CA compiler page headers (PP, IEL, IGY, IDM…)
    - Timestamp banner lines
    - Form-feed page breaks
    Keeps original line ordering — line numbers stay meaningful.
    """
    out: list[str] = []
    for raw in text.splitlines():
        line = _RE_FORMFEED.sub("", raw)
        if not line.strip():
            out.append(line)
            continue
        if _RE_OBJ_HEX.match(line):
            continue
        if _RE_PAGE_HDR.match(line):
            continue
        if _RE_DATE_HDR.match(line):
            continue
        out.append(line)
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────────────
#  Context extraction helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_context(text: str, names: list[str] | str,
                     context: int = CONTEXT_LINES,
                     max_chars: int = MAX_SOURCE_CHARS) -> tuple[str, dict[str, int]]:
    """
    Return (snippet, hits_per_name).

    `names` can be a single string or a list (leaf + parent groups). The
    function searches each name (word-boundary, case-insensitive), merges
    all windows together, and returns the per-name occurrence count.

    If NO name has any hit, returns the original text truncated to max_chars
    (the caller then knows to mark this as "no occurrence found").
    """
    if isinstance(names, str):
        names = [names]
    names = [n for n in names if n]
    if not names:
        return text[:max_chars], {}

    lines = text.splitlines()
    n     = len(lines)

    hits_per_name: dict[str, int] = {}
    all_hits: list[int] = []
    for nm in names:
        pat = re.compile(r"\b" + re.escape(nm) + r"\b", re.IGNORECASE)
        idx = [i for i, ln in enumerate(lines) if pat.search(ln)]
        hits_per_name[nm] = len(idx)
        all_hits.extend(idx)

    if not all_hits:
        truncated = text[:max_chars]
        if len(text) > max_chars:
            truncated += f"\n[... truncated — {len(lines)} lines total ...]"
        return truncated, hits_per_name

    selected: list[bool] = [False] * n
    for idx in all_hits:
        lo = max(0, idx - context)
        hi = min(n, idx + context + 1)
        for j in range(lo, hi):
            selected[j] = True

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
    return snippet, hits_per_name


def _data_division_block(source: str, max_chars: int = MAX_DATA_DIV_CHARS) -> str:
    """
    Extract the FULL DATA DIVISION block (until PROCEDURE DIVISION).
    Capped by character count, NOT by line count — large copybooks now
    fit until the cap. Returns "" if no DATA DIVISION marker found.
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
    block = "\n".join(lines[start:end])
    if len(block) > max_chars:
        block = block[:max_chars] + f"\n[... truncated — DATA DIVISION block ...]"
    return block


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
    CAS A — Des règles ont été trouvées → liste les règles en bullet-list, remplis
             Contrôles et Remarques.
             NE PAS écrire "Non trouvé" ni le bloc ⚠️.
    CAS B — Aucune règle trouvée → écrire UNIQUEMENT :
             "⚠️ Non trouvé dans le code source — aucune alimentation détectée."
             NE PAS inventer de règles, NE PAS écrire de bullet-list vide.
    Si tu as écrit au moins une règle (bullet "- Règle …") → tu es dans le CAS A.
    N'écris JAMAIS une règle hypothétique du type "pourrait être alimenté par...".

R6. CHAMPS SOURCE EXACTS
    Quand un MOVE provient d'un champ source, cite le nom exact du champ source
    tel qu'il apparaît dans le code (respect de la casse COBOL).

R7. CONTRÔLES ET VALIDATIONS
    Liste uniquement les contrôles explicitement codés sur CE champ (IF, EVALUATE,
    88-level conditions, INSPECT). Ne déduis pas des contrôles implicites.

R8. LANGUE FRANÇAISE
    Toute la réponse est en français. Les noms de champs COBOL restent en majuscules.

R9. TRADUCTION POUR PUBLIC NON-COBOLIEN — OBLIGATOIRE
    L'audience de la spec ne connaît PAS le COBOL. Toujours fournir :

    9.1 Type équivalent SQL/moderne — convertir chaque PIC clause en type
        de base de données relationnelle ou langage moderne :
          PIC X(n)             → VARCHAR(n)       — chaîne de n caractères
          PIC X                → CHAR(1)          — caractère unique
          PIC 9(n)             → INTEGER          — entier sur n chiffres (BIGINT si n>9)
          PIC 9(n)V9(m)        → DECIMAL(n+m, m)  — nombre décimal
          PIC S9(n)            → INTEGER signé    — entier positif ou négatif
          PIC S9(n)V9(m) COMP-3→ DECIMAL(n+m, m)  — décimal packé (stockage compact)
          PIC 9(n) COMP        → INTEGER binaire  — entier binaire
          PIC 9(8) (date)      → DATE             — si nom évoque date (DT-, DATE, JOUR…)
          88-level             → ENUM             — domaine de valeurs énumérées

    9.2 Vocabulaire métier — éviter le jargon COBOL dans la description.
        Au lieu de "MOVE WS-MNT-HT TO MNT-TOTAL", écris :
          "Le montant total reçoit la valeur du montant hors taxes."
        Au lieu de "EVALUATE WHEN '01'", écris :
          "Lorsque le code statut vaut '01' (selon le code)."
        Conserver les noms COBOL en majuscules entre parenthèses pour traçabilité.

    9.3 Rôle fonctionnel — la **Description** doit expliquer À QUOI SERT
        le champ pour le métier (ex. "identifiant unique du client",
        "montant TTC d'une facture", "code statut du dossier") — pas sa
        nature COBOL.

═══════════════════════════════════════════════════════
FORMAT DE RÉPONSE — DEUX VARIANTES EXCLUSIVES, JAMAIS LES DEUX
═══════════════════════════════════════════════════════

▶ VARIANTE A — utilise ce format SI ET SEULEMENT SI tu trouves au moins
               une alimentation explicite dans le code :

### {field_heading}
**Nom technique**     : {field_name}
**Libellé métier**    : <libellé du commentaire, sinon "(non trouvé)">
**Type COBOL**        : <PIC clause exacte, ex: PIC X(006)>
**Type équivalent**   : <traduction SQL/moderne selon R9.1, ex: VARCHAR(6) — chaîne de 6 caractères>
**Rôle métier**       : <à quoi sert ce champ pour le métier, en langage clair (R9.3)>

**Alimentation** *(décrite en langage métier — R9.2)*
- Règle 1 : MOVE|COMPUTE|INITIALIZE|REDEFINES — Condition : Toujours|Si <condition verbatim> — <description claire en langage métier, avec noms COBOL entre parenthèses> [ligne X]
- Règle 2 : ... [ligne Y]

**Contrôles**
- <contrôle codé avec numéro de ligne, expliqué en langage métier>
- Aucun contrôle détecté.

**Remarques**
- <information structurelle utile au lecteur non-cobolien, sinon vide>

──────────────────────────────────────────────────────
▶ VARIANTE B — utilise ce format SI ET SEULEMENT SI aucune alimentation
               n'est trouvée dans le code :

### {field_heading}
**Nom technique**     : {field_name}
**Libellé métier**    : <libellé du commentaire, sinon "(non trouvé)">
**Type COBOL**        : <PIC clause exacte>
**Type équivalent**   : <traduction SQL/moderne selon R9.1>
**Rôle métier**       : <à quoi sert ce champ pour le métier, en langage clair>

**Alimentation**
⚠️ **Non trouvé dans le code source — aucune alimentation détectée.**
──────────────────────────────────────────────────────

⛔ INTERDIT : écrire à la fois des règles ET le message ⚠️ dans la même réponse.
   → Des règles présentes → VARIANTE A uniquement, pas de ligne ⚠️.
   → Aucune règle         → VARIANTE B uniquement, pas de section Contrôles/Remarques.
"""


def _build_prompt(
    field:        CopyField,
    source_cobol: str,
    compiled:     str,
    input_desc:   str,
    target_desc:  str,
    parents:      list[str] | None = None,
) -> tuple[str, dict]:
    """
    Builds the user prompt for one field and returns (prompt_text, diagnostic).

    diagnostic = {
        "direct_hits": int,           # occurrences of field.name in source
        "parent_hits": dict[str,int], # occurrences per parent group
        "compiled_direct_hits": int,
        "compiled_parent_hits": dict[str,int],
        "parents_used": list[str],
        "data_div_chars": int,
        "source_chars": int,
        "compiled_chars": int,
    }
    """
    parents = parents or []

    # PIC info
    pic_info = f"PIC {field.pic}" if field.pic else "(groupe, pas de PIC)"
    if field.usage:
        pic_info += f" USAGE {field.usage}"
    if field.occurs:
        pic_info += f" OCCURS {field.occurs}"
    if field.values_88:
        pic_info += f"\n   Valeurs 88 : {', '.join(field.values_88[:10])}"

    libelle = field.description or "(non trouvé dans les commentaires)"

    # Pre-filter the compiled listing once
    compiled_clean = _clean_listing(compiled) if compiled else ""

    # ── Source: search leaf + parents (parents get a smaller window) ───────
    names_all = [field.name] + parents
    src_snippet, src_hits = _extract_context(
        source_cobol, names_all, context=CONTEXT_LINES, max_chars=MAX_SOURCE_CHARS,
    )
    direct_hits = src_hits.get(field.name, 0)
    parent_hits = {p: src_hits.get(p, 0) for p in parents}

    if direct_hits:
        src_note = f"({direct_hits} occurrence(s) directe(s) du champ — fenêtre ±{CONTEXT_LINES} lignes)"
    elif any(parent_hits.values()):
        used = [p for p, n in parent_hits.items() if n]
        src_note = (f"(0 occurrence directe — recherche élargie aux groupes parents : "
                    f"{', '.join(used)} — voir lignes ci-dessous)")
    else:
        src_note = "(aucune occurrence directe ni via parents — source complet tronqué)"

    # ── Compiled listing: same logic ────────────────────────────────────────
    if compiled_clean:
        cmp_snippet, cmp_hits = _extract_context(
            compiled_clean, names_all, context=20, max_chars=MAX_COMPILED_CHARS,
        )
        cmp_direct = cmp_hits.get(field.name, 0)
        cmp_parent = {p: cmp_hits.get(p, 0) for p in parents}
        cmp_note = f"({cmp_direct} occurrence(s) du champ, {sum(cmp_parent.values())} via parents)"
    else:
        cmp_snippet = "(non fourni)"
        cmp_direct  = 0
        cmp_parent  = {}
        cmp_note    = "(non fourni)"

    # ── DATA DIVISION complète (cap par chars) ─────────────────────────────
    data_div = _data_division_block(source_cobol)
    data_section = (
        f"\n## DATA DIVISION complète (définitions de zones)\n```\n{data_div}\n```\n"
        if data_div else ""
    )

    parents_note = (
        f"Groupes parents de ce champ (à consulter si le leaf n'apparaît pas directement) : "
        f"{' → '.join(parents)}"
        if parents else "(aucun groupe parent détecté)"
    )

    occurrences_warning = ""
    if not direct_hits and not any(parent_hits.values()):
        occurrences_warning = (
            "\n⚠️ ATTENTION : aucune occurrence directe NI parent trouvée dans le code. "
            "Si vraiment rien → applique la VARIANTE B (Non trouvé).\n"
        )
    elif not direct_hits:
        occurrences_warning = (
            "\n💡 INDICE : ce champ n'apparaît PAS directement, mais son/ses groupe(s) "
            f"parent(s) ({', '.join(p for p in parents if parent_hits.get(p))}) "
            "sont référencés. Cherche des MOVE/INITIALIZE sur le groupe parent — "
            "ils alimentent indirectement ce champ.\n"
        )

    prompt = f"""\
## CHAMP À SPÉCIFIER
┌─────────────────────────────────────────────────────────────┐
│ Nom technique : {field.name:<44} │
│ Libellé métier: {libelle:<44} │
│ Type (PIC)    : {pic_info:<44} │
│ Niveau COBOL  : {field.level:<44} │
└─────────────────────────────────────────────────────────────┘
{parents_note}

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
{src_snippet}
```

## Extraits du listing compilé (nettoyé) {cmp_note}
```
{cmp_snippet}
```

---
TITRE OBLIGATOIRE de ta réponse (copie cette ligne EXACTEMENT, telle quelle,
sans la modifier) :

### {field.name} — {libelle.upper()}

Produis maintenant la spécification d'alimentation du champ **{field.name}** \
({libelle}) en respectant STRICTEMENT le format et les règles R1 à R9.
"""

    diagnostic = {
        "direct_hits":          direct_hits,
        "parent_hits":          parent_hits,
        "compiled_direct_hits": cmp_direct,
        "compiled_parent_hits": cmp_parent,
        "parents_used":         parents,
        "data_div_chars":       len(data_div),
        "source_chars":         len(src_snippet),
        "compiled_chars":       len(cmp_snippet) if compiled_clean else 0,
    }
    return prompt, diagnostic


# ─────────────────────────────────────────────────────────────────────────────
#  Agent function
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FieldSpec:
    field_name: str
    markdown:   str
    found:      bool                  # False if "non trouvé" in output
    diagnostic: dict | None = None    # context metrics (occurrences, chars sent)


def specify_field(
    field:        CopyField,
    source_cobol: str,
    compiled:     str,
    input_desc:   str,
    target_desc:  str,
    parents:      list[str] | None = None,
    temperature:  float = 0.0,
) -> FieldSpec:
    """
    Calls the LLM to produce the alimentation spec for one target field.
    Returns a FieldSpec with the raw Markdown + a diagnostic dict.
    """
    client = build_client()
    model  = get_model("doc")

    # Build the composite heading: "F1788 — CODE TYPE USAGE CONTREPARTIE"
    label = (field.description or "").strip().upper()
    heading = f"{field.name} — {label}" if label else field.name

    system = SYSTEM_PROMPT.replace("{field_name}", field.name)
    system = system.replace("{field_heading}", heading)
    user, diagnostic = _build_prompt(
        field, source_cobol, compiled, input_desc, target_desc, parents=parents,
    )

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

    return FieldSpec(field_name=field.name, markdown=md, found=found,
                     diagnostic=diagnostic)
