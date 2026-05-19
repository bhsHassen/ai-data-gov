"""
COBOL Reverse Engineering Dashboard — visual cartography + specification viewer.

Routes:
    GET  /                    main HTML shell
    GET  /api/inspect         scans input/raw/, returns inspection JSON
    GET  /api/mermaid         returns Mermaid source for the call/copy graph
    GET  /api/spec/<name>     returns a generated spec file from output/
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from flask import Flask, jsonify, Response
from dotenv import load_dotenv

from src.cobol_reverse.inspect import inspect_directory, save_report
from src.cobol_reverse.console import log

load_dotenv()

app = Flask(__name__)
INPUT_DIR  = Path("input/raw")
OUTPUT_DIR = Path("output")


# ─────────────────────────────────────────────────────────────────────────────
#  Mermaid graph builder — derives call/copy graph from inspection report
# ─────────────────────────────────────────────────────────────────────────────

def _build_mermaid(reports: list[dict]) -> str:
    """
    Builds a Mermaid flowchart LR from the inspection report.
    Nodes:
      - COBOL programs → rounded rectangle  pgm_NAME([NAME])
      - Copybooks       → cylinder          cpb_NAME[(NAME)]
      - Unknown         → hexagon           unk_NAME{{NAME}}
    Edges:
      - CALL  → solid arrow
      - COPY  → dashed arrow
    """
    lines = ["flowchart LR"]

    # Collect known names for type lookup
    name_to_type: dict[str, str] = {}
    for r in reports:
        key = Path(r["path"]).stem.upper()
        name_to_type[key] = r["file_type"]
        if r.get("program_id"):
            name_to_type[r["program_id"]] = r["file_type"]

    def node_id(name: str) -> str:
        return re.sub(r"[^A-Za-z0-9]", "_", name)

    def node_def(name: str, ftype: str) -> str:
        nid = node_id(name)
        label = name
        if ftype == "cobol":
            return f'  {nid}(["{label}"])'
        if ftype == "copybook":
            return f'  {nid}[("{label}")]'
        return f'  {nid}{{"{label}"}}'

    # Emit node definitions
    lines.append("")
    lines.append("  %% — Nodes —")
    defined: set[str] = set()
    for r in reports:
        display_name = r.get("program_id") or Path(r["path"]).stem.upper()
        ftype = r["file_type"]
        nid   = node_id(display_name)
        if nid not in defined:
            lines.append(node_def(display_name, ftype))
            defined.add(nid)

    # Emit CALL edges (program → program)
    lines.append("")
    lines.append("  %% — CALL edges —")
    for r in reports:
        if r["file_type"] != "cobol":
            continue
        src_name = r.get("program_id") or Path(r["path"]).stem.upper()
        for called in r.get("static_calls", []):
            tgt_type = name_to_type.get(called, "unknown")
            tgt_nid  = node_id(called)
            if tgt_nid not in defined:
                lines.append(node_def(called, tgt_type))
                defined.add(tgt_nid)
            lines.append(f'  {node_id(src_name)} -->|"CALL"| {tgt_nid}')

    # Emit COPY edges (program → copybook)
    lines.append("")
    lines.append("  %% — COPY edges —")
    for r in reports:
        if r["file_type"] not in ("cobol", "copybook"):
            continue
        src_name = r.get("program_id") or Path(r["path"]).stem.upper()
        for cpb in r.get("copy_includes", []):
            tgt_type = name_to_type.get(cpb, "copybook")
            tgt_nid  = node_id(cpb)
            if tgt_nid not in defined:
                lines.append(node_def(cpb, tgt_type))
                defined.add(tgt_nid)
            lines.append(f'  {node_id(src_name)} -. "COPY" .-> {tgt_nid}')

    # Styles
    lines.append("")
    lines.append("  %% — Styles —")
    lines.append("  classDef cobol    fill:#dbeafe,stroke:#2563eb,color:#1e3a8a,font-weight:bold")
    lines.append("  classDef copybook fill:#dcfce7,stroke:#16a34a,color:#14532d")
    lines.append("  classDef jcl      fill:#fef9c3,stroke:#ca8a04,color:#713f12")
    lines.append("  classDef unknown  fill:#f1f5f9,stroke:#94a3b8,color:#475569")

    # Assign classes
    for r in reports:
        display_name = r.get("program_id") or Path(r["path"]).stem.upper()
        nid  = node_id(display_name)
        ftype = r["file_type"]
        lines.append(f"  class {nid} {ftype}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
#  Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/inspect")
def api_inspect():
    if not INPUT_DIR.exists() or not any(INPUT_DIR.iterdir()):
        return jsonify({"error": "no_files",
                        "message": f"Aucun fichier trouvé dans {INPUT_DIR.resolve()}"}), 404
    try:
        from src.cobol_reverse.inspect import inspect_directory
        from dataclasses import asdict
        reports = inspect_directory(INPUT_DIR)
        save_report(reports, OUTPUT_DIR / "inspect.json")
        return jsonify({"reports": [asdict(r) for r in reports]})
    except Exception as e:
        log("error", str(e))
        return jsonify({"error": str(e)}), 500


@app.route("/api/mermaid")
def api_mermaid():
    inspect_json = OUTPUT_DIR / "inspect.json"
    if not inspect_json.exists():
        return jsonify({"error": "run_inspect_first"}), 404
    data = json.loads(inspect_json.read_text(encoding="utf-8"))
    mermaid_src = _build_mermaid(data.get("files", []))
    return jsonify({"mermaid": mermaid_src})


@app.route("/api/specs")
def api_specs():
    specs = sorted(OUTPUT_DIR.glob("*.md"))
    return jsonify({"specs": [p.name for p in specs]})


@app.route("/api/spec/<path:name>")
def api_spec(name: str):
    if ".." in name or not name.endswith(".md"):
        return jsonify({"error": "invalid"}), 400
    path = OUTPUT_DIR / name
    if not path.exists():
        return jsonify({"error": "not_found"}), 404
    return Response(path.read_text(encoding="utf-8"), mimetype="text/plain")


@app.route("/")
def index():
    return Response(DASHBOARD_HTML, mimetype="text/html")


# ─────────────────────────────────────────────────────────────────────────────
#  HTML
# ─────────────────────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CERISE — Reverse Engineering COBOL</title>

<!-- Mermaid (call graph rendering) -->
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>

<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Arial,sans-serif;background:#f0f4f8;color:#222;font-size:14px}

/* ── Banner ─────────────────────────────────────────────────── */
.banner{
  background:linear-gradient(135deg,#0041b4 0%,#0066cc 100%);
  color:#fff;padding:20px 32px;
  box-shadow:0 2px 8px rgba(0,0,0,.25);
  display:flex;align-items:center;gap:16px}
.banner-title{font-size:22px;font-weight:bold;letter-spacing:.4px}
.banner-sub{font-size:13px;opacity:.75;margin-top:3px}
.banner-badge{
  margin-left:auto;background:rgba(255,255,255,.15);
  border:1px solid rgba(255,255,255,.3);
  padding:5px 14px;font-size:12px;border-radius:20px}

/* ── Layout ─────────────────────────────────────────────────── */
.layout{display:flex;height:calc(100vh - 68px)}

/* ── Sidebar ─────────────────────────────────────────────────── */
.sidebar{
  width:280px;min-width:220px;background:#fff;
  border-right:1px solid #d1d9e6;
  display:flex;flex-direction:column;overflow:hidden}
.sidebar-head{
  padding:14px 16px;font-size:11px;font-weight:bold;
  text-transform:uppercase;letter-spacing:.8px;
  color:#6b7280;border-bottom:1px solid #eee;
  display:flex;align-items:center;justify-content:space-between}
.sidebar-list{flex:1;overflow-y:auto;padding:8px 0}
.file-item{
  padding:9px 16px;cursor:pointer;border-left:3px solid transparent;
  transition:background .12s}
.file-item:hover{background:#f0f4ff}
.file-item.active{background:#eff6ff;border-left-color:#2563eb}
.file-name{font-size:13px;font-weight:600;color:#1e293b;
           white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.file-meta{font-size:11px;color:#94a3b8;margin-top:2px}
.type-badge{
  display:inline-block;padding:1px 7px;border-radius:10px;
  font-size:10px;font-weight:bold;text-transform:uppercase;
  margin-right:4px}
.badge-cobol   {background:#dbeafe;color:#1d4ed8}
.badge-copybook{background:#dcfce7;color:#15803d}
.badge-jcl     {background:#fef9c3;color:#854d0e}
.badge-unknown {background:#f1f5f9;color:#475569}
.conf-high  {color:#15803d}
.conf-medium{color:#b45309}
.conf-low   {color:#dc2626}

.sidebar-actions{padding:12px 16px;border-top:1px solid #eee}
.btn-scan{
  width:100%;padding:9px;font-size:13px;font-weight:bold;
  background:#2563eb;color:#fff;border:none;cursor:pointer;
  border-radius:4px;transition:background .15s}
.btn-scan:hover{background:#1d4ed8}
.btn-scan:disabled{background:#93c5fd;cursor:not-allowed}

/* ── Main area ──────────────────────────────────────────────── */
.main{flex:1;display:flex;flex-direction:column;overflow:hidden}

/* ── Tabs ───────────────────────────────────────────────────── */
.tabs{display:flex;background:#fff;border-bottom:2px solid #e2e8f0;padding:0 24px}
.tab{
  padding:12px 20px;font-size:13px;font-weight:600;cursor:pointer;
  color:#64748b;border-bottom:3px solid transparent;margin-bottom:-2px;
  transition:all .15s}
.tab:hover{color:#2563eb}
.tab.active{color:#2563eb;border-bottom-color:#2563eb}

/* ── Tab panels ─────────────────────────────────────────────── */
.panel{display:none;flex:1;overflow:auto;padding:24px}
.panel.active{display:flex;flex-direction:column;gap:16px}

/* ── Cards ──────────────────────────────────────────────────── */
.card{background:#fff;border:1px solid #e2e8f0;border-radius:6px;padding:20px}
.card-title{font-size:13px;font-weight:bold;text-transform:uppercase;
            letter-spacing:.6px;color:#64748b;margin-bottom:14px;
            display:flex;align-items:center;gap:8px}
.card-title::before{content:'';display:block;width:3px;height:14px;
                    background:#2563eb;border-radius:2px}

/* ── Mermaid graph card ─────────────────────────────────────── */
.graph-card{background:#fff;border:1px solid #e2e8f0;border-radius:6px;
            padding:20px;min-height:320px;overflow:auto}
.mermaid-wrap{min-height:260px;display:flex;align-items:center;
              justify-content:center}
.graph-placeholder{
  color:#94a3b8;font-size:13px;text-align:center;padding:40px}
.graph-placeholder .icon{font-size:48px;margin-bottom:12px}

/* ── Stats row ──────────────────────────────────────────────── */
.stats{display:flex;gap:12px;flex-wrap:wrap}
.stat-box{
  flex:1;min-width:120px;background:#fff;border:1px solid #e2e8f0;
  border-radius:6px;padding:16px 20px;text-align:center}
.stat-num{font-size:28px;font-weight:bold;color:#2563eb}
.stat-lbl{font-size:11px;color:#94a3b8;text-transform:uppercase;
          letter-spacing:.6px;margin-top:4px}
.stat-box.type-cobol    .stat-num{color:#1d4ed8}
.stat-box.type-copybook .stat-num{color:#15803d}
.stat-box.type-jcl      .stat-num{color:#854d0e}
.stat-box.type-unknown  .stat-num{color:#64748b}

/* ── File detail table ──────────────────────────────────────── */
.detail-table{width:100%;border-collapse:collapse;font-size:12px}
.detail-table th{
  background:#f8fafc;color:#475569;font-weight:600;text-align:left;
  padding:8px 12px;border:1px solid #e2e8f0;font-size:11px;
  text-transform:uppercase;letter-spacing:.4px}
.detail-table td{
  padding:8px 12px;border:1px solid #e2e8f0;vertical-align:top;
  color:#374151;line-height:1.5}
.detail-table tr:nth-child(even) td{background:#f8fafc}
.detail-table .mono{font-family:Consolas,monospace;font-size:11px}
.tag-list{display:flex;flex-wrap:wrap;gap:4px}
.tag{background:#eff6ff;color:#1d4ed8;padding:2px 8px;
     border-radius:10px;font-size:10px;font-family:Consolas,monospace}
.tag.warn{background:#fef3c7;color:#92400e}
.tag.danger{background:#fee2e2;color:#991b1b}
.tag.green{background:#dcfce7;color:#166534}

/* ── Spec viewer ─────────────────────────────────────────────── */
.spec-toolbar{display:flex;gap:8px;margin-bottom:12px;align-items:center;flex-wrap:wrap}
.spec-select{
  padding:6px 10px;border:1px solid #d1d5db;font-size:13px;
  border-radius:4px;min-width:260px;color:#374151}
.spec-content{
  background:#fff;border:1px solid #e2e8f0;border-radius:6px;
  padding:32px 40px;max-width:900px;font-family:Georgia,serif;
  font-size:14px;line-height:1.7;color:#1e293b}
.spec-content h1{font-size:22px;margin-bottom:24px;color:#0f172a;
                 border-bottom:2px solid #2563eb;padding-bottom:10px}
.spec-content h2{font-size:16px;font-weight:bold;margin:28px 0 10px;
                 border-left:3px solid #2563eb;padding-left:8px;color:#1e3a8a}
.spec-content h3{font-size:14px;font-weight:bold;margin:16px 0 6px;color:#334155}
.spec-content p{margin-bottom:10px}
.spec-content table{width:100%;border-collapse:collapse;font-size:12px;margin:10px 0 16px}
.spec-content th{background:#f0f4f8;padding:8px 12px;border:1px solid #d1d9e6;
                 font-size:11px;text-transform:uppercase;letter-spacing:.4px}
.spec-content td{padding:7px 12px;border:1px solid #e2e8f0}
.spec-content code{background:#f1f5f9;padding:1px 5px;
                   font-family:Consolas,monospace;font-size:12px;color:#be185d}
.spec-content pre{background:#f8fafc;border:1px solid #e2e8f0;padding:14px;
                  font-family:Consolas,monospace;font-size:12px;overflow:auto;
                  border-radius:4px;margin:10px 0}
.spec-empty{color:#94a3b8;font-size:13px;text-align:center;padding:60px;
            background:#fff;border:1px solid #e2e8f0;border-radius:6px}
.spec-empty .icon{font-size:40px;margin-bottom:12px}

/* ── Source preview ─────────────────────────────────────────── */
.source-panel{background:#1e293b;border-radius:6px;padding:0;overflow:hidden}
.source-header{
  background:#0f172a;padding:8px 16px;font-size:11px;
  color:#94a3b8;font-family:Consolas,monospace;
  display:flex;justify-content:space-between}
.source-lines{
  margin:0;padding:12px 0;
  font-family:'Cascadia Code',Consolas,monospace;
  font-size:12px;line-height:1.5;color:#e2e8f0;
  max-height:340px;overflow-y:auto;
  counter-reset:sln}
.source-line{
  display:block;counter-increment:sln;
  padding:0 16px 0 56px;position:relative;white-space:pre}
.source-line::before{
  content:counter(sln);position:absolute;left:0;width:42px;
  text-align:right;padding-right:10px;
  color:#475569;user-select:none;
  border-right:1px solid #334155}
.source-line:hover{background:#263348}

/* ── Empty / loader states ──────────────────────────────────── */
.empty-state{
  text-align:center;color:#94a3b8;padding:60px 24px}
.empty-state .icon{font-size:52px;margin-bottom:14px}
.empty-state h3{font-size:16px;color:#64748b;margin-bottom:8px}
.empty-state p{font-size:13px}
.loader{
  display:inline-block;width:18px;height:18px;
  border:2px solid rgba(255,255,255,.4);
  border-top-color:#fff;border-radius:50%;
  animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>

<!-- ── Banner ───────────────────────────────────────────────── -->
<div class="banner">
  <div>
    <div class="banner-title">&#9670; CERISE &mdash; Reverse Engineering Mainframe</div>
    <div class="banner-sub">Cartographie &amp; sp&eacute;cification de code COBOL / VSAM</div>
  </div>
  <div class="banner-badge" id="scan-status">En attente de fichiers</div>
</div>

<!-- ── Layout ───────────────────────────────────────────────── -->
<div class="layout">

  <!-- Sidebar -->
  <div class="sidebar">
    <div class="sidebar-head">
      <span>Modules</span>
      <span id="file-count" style="color:#2563eb">0</span>
    </div>
    <div class="sidebar-list" id="sidebar-list">
      <div class="empty-state" style="padding:30px 16px">
        <div class="icon">&#128193;</div>
        <p style="font-size:12px">D&eacute;posez vos fichiers<br>dans <code>input/raw/</code><br>puis cliquez Analyser</p>
      </div>
    </div>
    <div class="sidebar-actions">
      <button class="btn-scan" id="btn-scan" onclick="runScan()">
        &#128269; Analyser les fichiers
      </button>
    </div>
  </div>

  <!-- Main -->
  <div class="main">

    <!-- Tabs -->
    <div class="tabs">
      <div class="tab active" onclick="showTab('cartographie')">&#127758; Cartographie</div>
      <div class="tab"        onclick="showTab('donnees')">&#128202; Donn&eacute;es</div>
      <div class="tab"        onclick="showTab('specification')">&#128196; Sp&eacute;cification</div>
      <div class="tab"        onclick="showTab('sources')">&#128195; Sources</div>
    </div>

    <!-- ── CARTOGRAPHIE ─────────────────────────────────────── -->
    <div class="panel active" id="panel-cartographie">

      <div class="stats" id="stats-row">
        <div class="stat-box type-cobol">
          <div class="stat-num" id="stat-cobol">—</div>
          <div class="stat-lbl">Programmes COBOL</div>
        </div>
        <div class="stat-box type-copybook">
          <div class="stat-num" id="stat-copybook">—</div>
          <div class="stat-lbl">Copybooks</div>
        </div>
        <div class="stat-box type-jcl">
          <div class="stat-num" id="stat-jcl">—</div>
          <div class="stat-lbl">JCL Jobs</div>
        </div>
        <div class="stat-box type-unknown">
          <div class="stat-num" id="stat-unknown">—</div>
          <div class="stat-lbl">Inconnus</div>
        </div>
      </div>

      <div class="graph-card">
        <div class="card-title">Graphe d&apos;appel &amp; d&eacute;pendances COPY</div>
        <div class="mermaid-wrap" id="graph-wrap">
          <div class="graph-placeholder">
            <div class="icon">&#9906;</div>
            <div>Lancez l&apos;analyse pour g&eacute;n&eacute;rer le graphe</div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-title">Source Mermaid</div>
        <pre id="mermaid-src" style="background:#f8fafc;border:1px solid #e2e8f0;
             padding:14px;font-family:Consolas,monospace;font-size:11px;
             overflow:auto;max-height:200px;color:#374151;border-radius:4px">
—</pre>
      </div>
    </div>

    <!-- ── DONNÉES ──────────────────────────────────────────── -->
    <div class="panel" id="panel-donnees">
      <div class="card">
        <div class="card-title">Inventaire des modules</div>
        <div id="data-table-wrap">
          <div class="empty-state">
            <div class="icon">&#128202;</div>
            <h3>Aucune donn&eacute;e</h3>
            <p>Lancez l&apos;analyse depuis la barre lat&eacute;rale.</p>
          </div>
        </div>
      </div>
    </div>

    <!-- ── SPECIFICATION ────────────────────────────────────── -->
    <div class="panel" id="panel-specification">
      <div class="spec-toolbar">
        <select class="spec-select" id="spec-select" onchange="loadSpec(this.value)">
          <option value="">— choisir une sp&eacute;cification —</option>
        </select>
        <button class="btn-scan" style="width:auto;padding:6px 18px"
                onclick="refreshSpecs()">&#8635; Rafra&icirc;chir</button>
      </div>
      <div id="spec-viewer">
        <div class="spec-empty">
          <div class="icon">&#128196;</div>
          <p>Les sp&eacute;cifications g&eacute;n&eacute;r&eacute;es appara&icirc;tront ici.</p>
          <p style="margin-top:8px;font-size:12px">Placez des fichiers <code>.md</code> dans <code>output/</code></p>
        </div>
      </div>
    </div>

    <!-- ── SOURCES ──────────────────────────────────────────── -->
    <div class="panel" id="panel-sources">
      <div class="card">
        <div class="card-title">Aperçu du source</div>
        <div id="source-viewer">
          <div class="empty-state">
            <div class="icon">&#128195;</div>
            <h3>S&eacute;lectionner un module</h3>
            <p>Cliquez sur un fichier dans la barre lat&eacute;rale.</p>
          </div>
        </div>
      </div>
    </div>

  </div><!-- /main -->
</div><!-- /layout -->

<script>
mermaid.initialize({
  startOnLoad: false,
  theme: "default",
  flowchart: { curve: "basis", useMaxWidth: true },
});

let _reports = [];
let _activeFile = null;

// ── Tab navigation ───────────────────────────────────────────
const TABS = ["cartographie","donnees","specification","sources"];
function showTab(name){
  TABS.forEach(t => {
    document.getElementById("panel-" + t).classList.toggle("active", t === name);
  });
  document.querySelectorAll(".tab").forEach((el, i) => {
    el.classList.toggle("active", TABS[i] === name);
  });
}

// ── Scan ─────────────────────────────────────────────────────
async function runScan(){
  const btn = document.getElementById("btn-scan");
  btn.disabled = true;
  btn.innerHTML = '<span class="loader"></span> Analyse...';
  document.getElementById("scan-status").textContent = "Analyse en cours…";

  try {
    const res  = await fetch("/api/inspect");
    if(!res.ok){
      const err = await res.json();
      alert("Erreur : " + (err.message || err.error));
      return;
    }
    const data = await res.json();
    _reports = data.reports || [];
    renderSidebar(_reports);
    renderStats(_reports);
    renderDataTable(_reports);
    await renderGraph();
    refreshSpecs();
    document.getElementById("scan-status").textContent =
      _reports.length + " fichier(s) analysé(s)";
  } catch(e){
    alert("Erreur réseau : " + e);
  } finally {
    btn.disabled = false;
    btn.innerHTML = "&#128269; Analyser les fichiers";
  }
}

// ── Sidebar ───────────────────────────────────────────────────
function renderSidebar(reports){
  const list = document.getElementById("sidebar-list");
  document.getElementById("file-count").textContent = reports.length;
  if(!reports.length){
    list.innerHTML = '<div class="empty-state" style="padding:30px 16px"><p>Aucun fichier reconnu</p></div>';
    return;
  }
  list.innerHTML = reports.map((r, i) => {
    const name  = r.path.split(/[/\\\\]/).pop();
    const pgm   = r.program_id || r.root_record || "";
    const lines = r.line_count.toLocaleString();
    const conf  = r.confidence;
    return `<div class="file-item" onclick="selectFile(${i})" id="fi-${i}">
      <div class="file-name">
        <span class="type-badge badge-${r.file_type}">${r.file_type}</span>${name}
      </div>
      <div class="file-meta">
        ${pgm ? '<b>' + escH(pgm) + '</b> · ' : ''}${lines} lignes
        <span class="conf-${conf}">[${conf}]</span>
      </div>
    </div>`;
  }).join("");
}

// ── Stats ─────────────────────────────────────────────────────
function renderStats(reports){
  ["cobol","copybook","jcl","unknown"].forEach(t => {
    const n = reports.filter(r => r.file_type === t).length;
    document.getElementById("stat-" + t).textContent = n;
  });
}

// ── Data table ────────────────────────────────────────────────
function renderDataTable(reports){
  const wrap = document.getElementById("data-table-wrap");
  if(!reports.length){ wrap.innerHTML = ""; return; }

  const rows = reports.map(r => {
    const name = r.path.split(/[\\\\/]/).pop();
    const calls = (r.static_calls||[]).map(c => `<span class="tag">${escH(c)}</span>`).join(" ");
    const copies = (r.copy_includes||[]).map(c => `<span class="tag green">${escH(c)}</span>`).join(" ");
    const flags = [];
    if(r.has_exec_sql)  flags.push('<span class="tag danger">SQL</span>');
    if(r.has_exec_cics) flags.push('<span class="tag warn">CICS</span>');
    const pct = Math.round((r.comment_ratio||0)*100);
    return `<tr>
      <td><span class="type-badge badge-${r.file_type}">${r.file_type}</span></td>
      <td class="mono">${escH(name)}</td>
      <td>${escH(r.program_id || r.root_record || "—")}</td>
      <td>${r.line_count.toLocaleString()}</td>
      <td class="mono">${r.encoding}</td>
      <td><div class="tag-list">${copies || "—"}</div></td>
      <td><div class="tag-list">${calls  || "—"}</div></td>
      <td>${flags.join(" ") || "—"}</td>
      <td>${pct}%</td>
    </tr>`;
  }).join("");

  wrap.innerHTML = `<table class="detail-table">
    <thead><tr>
      <th>Type</th><th>Fichier</th><th>PROGRAM-ID / Record</th>
      <th>Lignes</th><th>Encodage</th>
      <th>COPY</th><th>CALL</th><th>Tech.</th><th>%Comment.</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

// ── Graph ─────────────────────────────────────────────────────
async function renderGraph(){
  const wrap = document.getElementById("graph-wrap");
  const srcEl = document.getElementById("mermaid-src");
  try {
    const res  = await fetch("/api/mermaid");
    if(!res.ok){ wrap.innerHTML = '<div class="graph-placeholder"><div>Données insuffisantes pour le graphe</div></div>'; return; }
    const data = await res.json();
    const src  = data.mermaid || "";
    srcEl.textContent = src;
    wrap.innerHTML = `<div class="mermaid">${escH(src)}</div>`;
    await mermaid.run({ nodes: wrap.querySelectorAll(".mermaid") });
  } catch(e){
    wrap.innerHTML = `<div class="graph-placeholder"><div>Erreur de rendu : ${escH(String(e))}</div></div>`;
  }
}

// ── File detail ───────────────────────────────────────────────
function selectFile(i){
  _activeFile = i;
  document.querySelectorAll(".file-item").forEach((el, j) => {
    el.classList.toggle("active", j === i);
  });
  renderSourcePreview(_reports[i]);
  showTab("sources");
}

function renderSourcePreview(r){
  const wrap = document.getElementById("source-viewer");
  const name = r.path.split(/[\\\\/]/).pop();
  const lines = (r.first_lines || []).map(l =>
    `<span class="source-line">${escH(l)}</span>`).join("");

  wrap.innerHTML = `
    <div class="source-panel">
      <div class="source-header">
        <span>${escH(name)}</span>
        <span>${r.line_count.toLocaleString()} lignes · ${r.encoding}</span>
      </div>
      <pre class="source-lines">${lines}</pre>
    </div>
    <div style="margin-top:12px;font-size:11px;color:#94a3b8;padding:4px">
      Affichage des 20 premières lignes — parsers complets à venir
    </div>`;
}

// ── Spec viewer ───────────────────────────────────────────────
async function refreshSpecs(){
  try {
    const res  = await fetch("/api/specs");
    const data = await res.json();
    const sel  = document.getElementById("spec-select");
    const cur  = sel.value;
    sel.innerHTML = '<option value="">— choisir une spécification —</option>' +
      (data.specs||[]).map(s => `<option value="${escH(s)}">${escH(s)}</option>`).join("");
    if(cur && data.specs.includes(cur)) sel.value = cur;
  } catch(e){}
}

async function loadSpec(name){
  const viewer = document.getElementById("spec-viewer");
  if(!name){
    viewer.innerHTML = '<div class="spec-empty"><div class="icon">&#128196;</div><p>Sélectionnez une spécification.</p></div>';
    return;
  }
  try {
    const res  = await fetch("/api/spec/" + encodeURIComponent(name));
    if(!res.ok){ viewer.innerHTML = '<div class="spec-empty"><p>Fichier introuvable.</p></div>'; return; }
    const md = await res.text();
    viewer.innerHTML = '<div class="spec-content">' + renderMd(md) + '</div>';
  } catch(e){
    viewer.innerHTML = `<div class="spec-empty"><p>Erreur : ${escH(String(e))}</p></div>`;
  }
}

// ── Minimal Markdown renderer ─────────────────────────────────
function renderMd(md){
  return md
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/^# (.+)$/gm, "<h1>$1</h1>")
    .replace(/^## (.+)$/gm,"<h2>$1</h2>")
    .replace(/^### (.+)$/gm,"<h3>$1</h3>")
    .replace(/[*][*](.+?)[*][*]/g,"<strong>$1</strong>")
    .replace(/`([^`]+)`/g,"<code>$1</code>")
    .replace(/^---$/gm,"<hr>")
    .replace(/^- (.+)$/gm,"<li>$1</li>")
    .replace(/(<li>[\s\S]+?<\\/li>)/g, s => "<ul>" + s + "</ul>")
    .replace(/\\n/g,"<br>");
}

function escH(s){
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

// Auto-scan on load if files are present
window.addEventListener("DOMContentLoaded", () => { runScan(); });
</script>
</body>
</html>
"""


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log("info", "dashboard starting on http://127.0.0.1:5001")
    app.run(host="0.0.0.0", port=5001, debug=False)
