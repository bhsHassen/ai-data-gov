"""
Migration pipeline — orchestrates the 5 migration agents to produce a
single MIGRATION.md per project.

Inputs:
    - <project>/source.cbl, compiled.txt, input.cpy, target.cpy
    - output/<PROJECT>_SPEC.md  (must exist — field-by-field spec)

Output:
    - output/<PROJECT>_MIGRATION.md

Events emitted on the SSE queue:
    {"type": "start",       "agents": [...]}
    {"type": "agent_start", "agent": "<name>", "index": i, "total": N}
    {"type": "agent_done",  "agent": "<name>", "index": i, "markdown": "..."}
    {"type": "done",        "output": "path"}
    {"type": "error",       "message": "..."}
"""
from __future__ import annotations

import queue
from datetime import datetime
from pathlib import Path

from .console import log
from .pipeline import InputBundle, OUTPUT_DIR
from .agents.program_summary    import summarize_program
from .agents.flow_tracer        import trace_flow
from .agents.ddl_generator      import generate_ddl
from .agents.pseudo_coder       import generate_pseudo_code
from .agents.migration_concerns import list_concerns


# ─────────────────────────────────────────────────────────────────────────────
#  Agent registry — order = order of execution and output sections
# ─────────────────────────────────────────────────────────────────────────────

AGENTS = [
    ("program_summary",    "Préambule du programme"),
    ("flow_tracer",        "Cartographie INPUT → TARGET"),
    ("ddl_generator",      "Modèle de données SQL (DDL)"),
    ("pseudo_coder",       "Pseudo-code algorithmique"),
    ("migration_concerns", "Points d'attention migration"),
]


def _spec_path(project_name: str) -> Path:
    return OUTPUT_DIR / f"{project_name.upper()}_SPEC.md"


def run_migration_pipeline(project_folder: Path,
                           q: queue.Queue | None = None) -> Path:
    """
    Generate the migration document for one project.
    Requires the field-by-field SPEC.md to exist beforehand.
    """
    def emit(event: dict):
        if q:
            q.put(event)

    project_name = project_folder.name
    log("migration", f"starting migration pipeline for {project_name}")

    # ── Load inputs ─────────────────────────────────────────────────────── #
    bundle  = InputBundle(project_folder)
    missing = bundle.validate()
    if missing:
        msg = "Fichiers manquants : " + ", ".join(missing)
        emit({"type": "error", "message": msg})
        raise FileNotFoundError(msg)

    spec_path = _spec_path(project_name)
    if not spec_path.exists():
        msg = (f"Spécification champ-par-champ introuvable : {spec_path.name}. "
               f"Lance d'abord l'onglet 'Spécification'.")
        emit({"type": "error", "message": msg})
        raise FileNotFoundError(msg)

    field_spec  = spec_path.read_text(encoding="utf-8")
    source      = bundle.source
    compiled    = bundle.compiled
    input_desc  = bundle.input_desc
    target_desc = bundle.target_desc

    emit({"type": "start", "agents": [{"key": k, "label": l} for k, l in AGENTS]})

    sections: dict[str, str] = {}

    # ── Run each agent ──────────────────────────────────────────────────── #
    for i, (key, label) in enumerate(AGENTS, 1):
        log("migration", f"[{i}/{len(AGENTS)}] {key} — {label}")
        emit({"type": "agent_start", "agent": key, "label": label,
              "index": i, "total": len(AGENTS)})
        try:
            if key == "program_summary":
                md = summarize_program(source, compiled)
            elif key == "flow_tracer":
                md = trace_flow(input_desc, target_desc, field_spec, source)
            elif key == "ddl_generator":
                md = generate_ddl(target_desc, field_spec, project_name)
            elif key == "pseudo_coder":
                md = generate_pseudo_code(source)
            elif key == "migration_concerns":
                md = list_concerns(source, target_desc, input_desc)
            else:
                md = f"_(agent {key} non implémenté)_"

            sections[key] = md
            emit({"type": "agent_done", "agent": key, "label": label,
                  "index": i, "total": len(AGENTS), "markdown": md})
            log("migration", f"  ✓ {key} — {len(md)} chars")

        except Exception as e:
            err_md = f"## {label}\n\n⚠️ **Erreur lors de la génération** : {e}"
            sections[key] = err_md
            emit({"type": "agent_done", "agent": key, "label": label,
                  "index": i, "total": len(AGENTS), "markdown": err_md,
                  "error": str(e)})
            log("error", f"  {key}: {e}")

    # ── Assemble final document ─────────────────────────────────────────── #
    ts  = datetime.now().strftime("%Y-%m-%d %H:%M")
    doc = f"""# Dossier de migration — {project_name}

> Généré le {ts}
> Produit à partir de `{spec_path.name}` et des fichiers source/compiled/input/target.

---

"""
    for key, _label in AGENTS:
        doc += sections.get(key, "") + "\n\n---\n\n"

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{project_name.upper()}_MIGRATION.md"
    out_path.write_text(doc, encoding="utf-8")

    log("migration", f"migration doc saved → {out_path}")
    emit({"type": "done", "output": str(out_path)})
    return out_path
