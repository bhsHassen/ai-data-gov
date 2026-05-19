"""
Field-by-field specification pipeline.

Inputs (from a project folder  input/<project>/):
    source.txt / source.cbl     — original COBOL source
    compiled.txt                — compiled listing
    input_desc.txt / input.cpy  — input copybook / structure description
    target_desc.txt / target.cpy— target copybook (fields to specify)

Process:
    1. Parse target copybook → ordered list of leaf fields
    2. For each leaf field → FieldSpecifier LLM agent
    3. Assemble all specs into a single Markdown document
    4. Save to output/<project>_SPEC.md

Events emitted on the queue for the dashboard (SSE):
    {"type": "start",        "total": N}
    {"type": "field_start",  "field": "NAME", "index": i, "total": N}
    {"type": "field_done",   "field": "NAME", "index": i, "found": bool, "markdown": "..."}
    {"type": "done",         "output": "path/to/spec.md"}
    {"type": "error",        "message": "..."}
"""
from __future__ import annotations

import queue
import re
from datetime import datetime
from pathlib import Path

from .console import log
from .parsers.copybook import parse_copybook_file, CopyField
from .agents.field_specifier import specify_field


# ─────────────────────────────────────────────────────────────────────────────
#  File discovery
# ─────────────────────────────────────────────────────────────────────────────

def _find(folder: Path, *candidates: str) -> Path | None:
    """Return the first existing candidate file in folder."""
    for name in candidates:
        p = folder / name
        if p.exists():
            return p
    return None


def _read(p: Path | None) -> str:
    if p is None:
        return ""
    raw = p.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


class InputBundle:
    """Holds the 4 input artefacts for one project."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.source_path  = _find(folder, "source.cbl", "source.txt", "source.cob")
        self.compiled_path= _find(folder, "compiled.txt", "compiled.lst", "listing.txt")
        self.input_path   = _find(folder, "input.cpy", "input_desc.txt",
                                  "input_desc.cpy", "input.txt")
        self.target_path  = _find(folder, "target.cpy", "target_desc.txt",
                                  "target_desc.cpy", "target.txt")

    def validate(self) -> list[str]:
        """Returns list of missing required files."""
        missing = []
        if not self.source_path:
            missing.append("source COBOL (source.cbl / source.txt)")
        if not self.target_path:
            missing.append("structure cible (target.cpy / target_desc.txt)")
        return missing

    @property
    def source(self)  -> str: return _read(self.source_path)
    @property
    def compiled(self)-> str: return _read(self.compiled_path)
    @property
    def input_desc(self) -> str: return _read(self.input_path)
    @property
    def target_desc(self)-> str: return _read(self.target_path)

    def target_fields(self) -> list[CopyField]:
        """Parse target copybook and return leaf fields only."""
        if not self.target_path:
            return []
        all_fields = parse_copybook_file(self.target_path)
        # Leaf = has a PIC clause (actual data field, not a group)
        leaves = [f for f in all_fields if not f.is_group]
        return leaves


# ─────────────────────────────────────────────────────────────────────────────
#  Assembler
# ─────────────────────────────────────────────────────────────────────────────

def _assemble(project_name: str, specs: list[dict]) -> str:
    """Build the final Markdown document from individual field specs."""
    found    = sum(1 for s in specs if s["found"])
    not_found= len(specs) - found
    ts       = datetime.now().strftime("%Y-%m-%d %H:%M")

    header = f"""# Spécification d'alimentation — {project_name}

> Généré le {ts}
> **{len(specs)} champs analysés** — {found} alimentés · {not_found} non trouvés dans le code

---

"""
    body = "\n\n---\n\n".join(s["markdown"] for s in specs)
    return header + body


# ─────────────────────────────────────────────────────────────────────────────
#  Main pipeline
# ─────────────────────────────────────────────────────────────────────────────

OUTPUT_DIR = Path("output")


def run_pipeline(
    project_folder: Path,
    q: queue.Queue | None = None,
) -> Path:
    """
    Run the full field-by-field specification pipeline.

    Args:
        project_folder: folder containing the 4 input files.
        q: optional queue for SSE events. Each event is a dict.

    Returns:
        Path to the generated spec file.
    """
    def emit(event: dict):
        if q:
            q.put(event)

    project_name = project_folder.name

    # ── Load inputs ─────────────────────────────────────────────────────── #
    log("pipeline", f"loading inputs from {project_folder}")
    bundle = InputBundle(project_folder)
    missing = bundle.validate()
    if missing:
        msg = "Fichiers manquants : " + ", ".join(missing)
        log("error", msg)
        emit({"type": "error", "message": msg})
        raise FileNotFoundError(msg)

    log("pipeline", f"source    : {bundle.source_path.name}")
    log("pipeline", f"compiled  : {bundle.compiled_path.name if bundle.compiled_path else '(absent)'}")
    log("pipeline", f"input desc: {bundle.input_path.name   if bundle.input_path    else '(absent)'}")
    log("pipeline", f"target    : {bundle.target_path.name}")

    # ── Parse target fields ──────────────────────────────────────────────── #
    target_fields = bundle.target_fields()
    if not target_fields:
        msg = "Aucun champ trouvé dans le copybook cible."
        emit({"type": "error", "message": msg})
        raise ValueError(msg)

    log("pipeline", f"{len(target_fields)} champs cible à spécifier")
    emit({"type": "start", "total": len(target_fields),
          "fields": [f.name for f in target_fields]})

    # ── Pre-load texts (once, not per field) ────────────────────────────── #
    source_cobol = bundle.source
    compiled     = bundle.compiled
    input_desc   = bundle.input_desc
    target_desc  = bundle.target_desc

    # ── Field-by-field LLM calls ─────────────────────────────────────────── #
    specs: list[dict] = []
    for i, fld in enumerate(target_fields, 1):
        log("doc", f"[{i}/{len(target_fields)}] {fld.name}")
        emit({"type": "field_start", "field": fld.name,
              "index": i, "total": len(target_fields)})
        try:
            result = specify_field(
                field        = fld,
                source_cobol = source_cobol,
                compiled     = compiled,
                input_desc   = input_desc,
                target_desc  = target_desc,
            )
            specs.append({
                "field_name": fld.name,
                "markdown":   result.markdown,
                "found":      result.found,
            })
            emit({"type": "field_done", "field": fld.name,
                  "index": i, "found": result.found,
                  "markdown": result.markdown})
            log("doc", f"  {'✓ trouvé' if result.found else '○ non trouvé'}")
        except Exception as e:
            log("error", f"  {fld.name}: {e}")
            fallback_md = f"### {fld.name}\\n\\n**Erreur** : {e}"
            specs.append({"field_name": fld.name, "markdown": fallback_md, "found": False})
            emit({"type": "field_done", "field": fld.name, "index": i,
                  "found": False, "markdown": fallback_md})

    # ── Assemble & save ──────────────────────────────────────────────────── #
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{project_name.upper()}_SPEC.md"
    doc = _assemble(project_name, specs)
    out_path.write_text(doc, encoding="utf-8")

    log("pipeline", f"spec saved → {out_path}")
    emit({"type": "done", "output": str(out_path),
          "found": sum(1 for s in specs if s["found"]),
          "total": len(specs)})

    return out_path
