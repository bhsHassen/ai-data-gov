"""
CERISE — Reverse Engineering COBOL
Dashboard Flask : upload des 4 fichiers + progression champ par champ + spec finale.

Routes:
    GET  /                           page principale
    GET  /api/projects               liste des projets dans input/
    POST /api/run/<project>          démarre le pipeline
    GET  /api/events/<run_id>        SSE : progression temps réel
    GET  /api/spec/<filename>        retourne un .md depuis output/
    GET  /api/fields/<project>       liste des champs cible parsés (preview)
"""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, request
from dotenv import load_dotenv

from src.cobol_reverse.console import log
from src.cobol_reverse.pipeline import run_pipeline, InputBundle

load_dotenv()

app       = Flask(__name__)
INPUT_DIR = Path("input")
OUTPUT_DIR= Path("output")

_runs: dict[str, queue.Queue] = {}
_runs_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────────
#  Background runner
# ─────────────────────────────────────────────────────────────────────────────

def _run_bg(run_id: str, project_folder: Path):
    with _runs_lock:
        q = _runs.get(run_id)
    if q is None:
        return
    try:
        run_pipeline(project_folder, q)
    except Exception as e:
        q.put({"type": "error", "message": str(e)})
    finally:
        q.put(None)   # sentinel


# ─────────────────────────────────────────────────────────────────────────────
#  SSE helper
# ─────────────────────────────────────────────────────────────────────────────

def _sse(run_id: str):
    def generate():
        with _runs_lock:
            q = _runs.get(run_id)
        if q is None:
            yield "data: {\"type\":\"error\",\"message\":\"run not found\"}\n\n"
            return
        while True:
            try:
                event = q.get(timeout=30)
            except queue.Empty:
                yield "data: {\"type\":\"heartbeat\"}\n\n"
                continue
            if event is None:
                yield "data: {\"type\":\"done_sentinel\"}\n\n"
                with _runs_lock:
                    _runs.pop(run_id, None)
                return
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


# ─────────────────────────────────────────────────────────────────────────────
#  API routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/projects")
def api_projects():
    INPUT_DIR.mkdir(exist_ok=True)
    projects = []
    for d in sorted(INPUT_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        b = InputBundle(d)
        projects.append({
            "name":     d.name,
            "source":   b.source_path.name  if b.source_path   else None,
            "compiled": b.compiled_path.name if b.compiled_path else None,
            "input":    b.input_path.name    if b.input_path    else None,
            "target":   b.target_path.name   if b.target_path   else None,
            "ready":    len(b.validate()) == 0,
            "spec":     (OUTPUT_DIR / f"{d.name.upper()}_SPEC.md").exists(),
        })
    return jsonify({"projects": projects})


@app.route("/api/fields/<project>")
def api_fields(project: str):
    folder = INPUT_DIR / project
    if not folder.exists():
        return jsonify({"error": "not found"}), 404
    b = InputBundle(folder)
    fields = b.target_fields()
    return jsonify({"fields": [
        {"name": f.name, "level": f.level,
         "pic": f.pic, "pic_type": f.pic_type,
         "is_group": f.is_group,
         "values_88": f.values_88}
        for f in fields
    ]})


@app.route("/api/run/<project>", methods=["POST"])
def api_run(project: str):
    folder = INPUT_DIR / project
    if not folder.exists():
        return jsonify({"error": "project not found"}), 404
    b = InputBundle(folder)
    missing = b.validate()
    if missing:
        return jsonify({"error": "missing files", "missing": missing}), 400

    run_id = str(uuid.uuid4())
    q: queue.Queue = queue.Queue()
    with _runs_lock:
        _runs[run_id] = q

    t = threading.Thread(target=_run_bg, args=(run_id, folder), daemon=True)
    t.start()
    return jsonify({"run_id": run_id})


@app.route("/api/events/<run_id>")
def api_events(run_id: str):
    return _sse(run_id)


@app.route("/api/spec/<path:filename>")
def api_spec(filename: str):
    if ".." in filename or not filename.endswith(".md"):
        return jsonify({"error": "invalid"}), 400
    p = OUTPUT_DIR / filename
    if not p.exists():
        return jsonify({"error": "not found"}), 404
    return Response(p.read_text(encoding="utf-8"), mimetype="text/plain")


@app.route("/api/specs")
def api_specs():
    OUTPUT_DIR.mkdir(exist_ok=True)
    return jsonify({"specs": [p.name for p in sorted(OUTPUT_DIR.glob("*.md"))]})


@app.route("/api/test-llm")
def api_test_llm():
    """Quick connectivity test — sends a minimal request to the LLM."""
    import os, time
    from src.cobol_reverse.llm import build_client, get_model
    try:
        t0     = time.time()
        client = build_client()
        model  = get_model("doc")
        resp   = client.chat.completions.create(
            model       = model,
            temperature = 0,
            max_tokens  = 64,
            extra_body  = {"enable_thinking": False},
            messages    = [{"role": "user", "content": "Reply with OK only."}],
        )
        elapsed = round((time.time() - t0) * 1000)
        msg     = resp.choices[0].message
        answer  = (msg.content or getattr(msg, "reasoning_content", None) or "(empty)").strip()
        return jsonify({
            "ok":      True,
            "model":   model,
            "base_url": os.getenv("LLM_BASE_URL","(not set)"),
            "reply":   answer,
            "ms":      elapsed,
        })
    except Exception as e:
        return jsonify({
            "ok":      False,
            "error":   str(e),
            "base_url": os.getenv("LLM_BASE_URL","(not set)"),
            "model":   os.getenv("LLM_MODEL","(not set)"),
        }), 502


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
<title>CERISE &mdash; Reverse Engineering COBOL</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Arial,sans-serif;background:#f0f4f8;color:#1e293b;font-size:14px}

.banner{background:linear-gradient(135deg,#0041b4,#0066cc);color:#fff;
        padding:18px 32px;box-shadow:0 2px 8px rgba(0,0,0,.2);
        display:flex;align-items:center;gap:14px}
.banner-title{font-size:21px;font-weight:bold;letter-spacing:.3px}
.banner-sub{font-size:12px;opacity:.75;margin-top:3px}

.layout{display:flex;height:calc(100vh - 65px)}

/* Sidebar */
.sidebar{width:300px;background:#fff;border-right:1px solid #d1d9e6;
         display:flex;flex-direction:column;overflow:hidden}
.sidebar-head{padding:12px 16px;font-size:11px;font-weight:bold;
              text-transform:uppercase;letter-spacing:.8px;color:#64748b;
              border-bottom:1px solid #eee;display:flex;
              justify-content:space-between;align-items:center}
.sidebar-body{flex:1;overflow-y:auto}

.project-card{padding:12px 16px;border-bottom:1px solid #f1f5f9;
              cursor:pointer;transition:background .12s}
.project-card:hover{background:#f8fafc}
.project-card.active{background:#eff6ff;border-left:3px solid #2563eb}
.proj-name{font-size:14px;font-weight:700;color:#1e293b}
.proj-files{margin-top:6px;display:flex;flex-wrap:wrap;gap:4px}
.file-chip{font-size:10px;padding:2px 7px;border-radius:10px;font-family:Consolas,monospace}
.chip-ok  {background:#dcfce7;color:#15803d}
.chip-miss{background:#fee2e2;color:#991b1b}
.chip-spec{background:#dbeafe;color:#1d4ed8}

.btn-new{margin:12px 16px;padding:8px;width:calc(100% - 32px);
         background:#f8fafc;border:1px dashed #94a3b8;color:#475569;
         font-size:12px;cursor:pointer;border-radius:4px}
.btn-new:hover{background:#eff6ff;border-color:#2563eb;color:#2563eb}

.btn-test-llm{margin:8px 16px;padding:7px;width:calc(100% - 32px);
              background:#f8fafc;border:1px solid #d1d9e6;color:#475569;
              font-size:11px;cursor:pointer;border-radius:4px;text-align:left}
.btn-test-llm:hover{background:#eff6ff;border-color:#2563eb;color:#2563eb}
.llm-status{margin:0 16px 10px;font-size:11px;padding:6px 10px;
            border-radius:4px;display:none;line-height:1.5}
.llm-ok  {background:#dcfce7;color:#15803d;display:block}
.llm-err {background:#fee2e2;color:#991b1b;display:block}

/* Main */
.main{flex:1;display:flex;flex-direction:column;overflow:hidden}
.tabs{display:flex;background:#fff;border-bottom:2px solid #e2e8f0;padding:0 24px}
.tab{padding:11px 18px;font-size:13px;font-weight:600;cursor:pointer;
     color:#64748b;border-bottom:3px solid transparent;margin-bottom:-2px;
     transition:all .15s}
.tab:hover{color:#2563eb}
.tab.active{color:#2563eb;border-bottom-color:#2563eb}

.panel{display:none;flex:1;overflow:auto;padding:24px;flex-direction:column;gap:16px}
.panel.active{display:flex}

/* Cards */
.card{background:#fff;border:1px solid #e2e8f0;border-radius:6px;padding:20px}
.card-title{font-size:12px;font-weight:bold;text-transform:uppercase;
            letter-spacing:.6px;color:#64748b;margin-bottom:14px;
            display:flex;align-items:center;gap:8px}
.card-title::before{content:'';display:block;width:3px;height:13px;
                    background:#2563eb;border-radius:2px}

/* Setup panel */
.setup-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.file-slot{border:1px solid #e2e8f0;border-radius:6px;padding:14px}
.slot-label{font-size:11px;font-weight:bold;text-transform:uppercase;
            letter-spacing:.5px;color:#64748b;margin-bottom:6px}
.slot-name{font-family:Consolas,monospace;font-size:12px;color:#1d4ed8}
.slot-miss{font-size:12px;color:#dc2626;font-style:italic}
.slot-desc{font-size:11px;color:#94a3b8;margin-top:4px}

.btn-run{padding:10px 28px;background:#2563eb;color:#fff;border:none;
         font-size:14px;font-weight:bold;cursor:pointer;border-radius:4px;
         transition:background .15s;display:flex;align-items:center;gap:8px}
.btn-run:hover{background:#1d4ed8}
.btn-run:disabled{background:#93c5fd;cursor:not-allowed}

/* Progress panel */
.progress-bar-wrap{background:#e2e8f0;border-radius:4px;height:8px;margin-bottom:16px}
.progress-bar{background:#2563eb;height:8px;border-radius:4px;
              transition:width .3s;width:0}
.progress-label{font-size:12px;color:#64748b;margin-bottom:6px}

.field-list{display:flex;flex-direction:column;gap:4px;max-height:55vh;overflow-y:auto}
.field-row{display:flex;align-items:center;gap:10px;padding:7px 12px;
           border-radius:4px;background:#f8fafc;font-size:12px}
.field-row.running{background:#eff6ff}
.field-row.done-ok {background:#f0fdf4}
.field-row.done-miss{background:#fef2f2}
.field-status{width:18px;height:18px;border-radius:50%;flex-shrink:0;
              display:flex;align-items:center;justify-content:center;font-size:11px}
.st-pending{background:#f1f5f9;color:#94a3b8}
.st-running{background:#dbeafe;color:#2563eb;animation:pulse 1s infinite}
.st-ok     {background:#dcfce7;color:#15803d}
.st-miss   {background:#fee2e2;color:#dc2626}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.field-name-cell{font-family:Consolas,monospace;font-weight:600;min-width:200px}
.field-pic-cell {color:#94a3b8;font-family:Consolas,monospace}

/* Spec panel */
.spec-content{background:#fff;border:1px solid #e2e8f0;border-radius:6px;
              padding:32px 44px;font-family:Georgia,serif;font-size:14px;
              line-height:1.75;color:#1e293b;max-width:920px}
.spec-content h1{font-size:22px;color:#0f172a;border-bottom:2px solid #2563eb;
                 padding-bottom:10px;margin-bottom:20px}
.spec-content h2{font-size:16px;font-weight:bold;margin:28px 0 8px;
                 border-left:3px solid #2563eb;padding-left:8px;color:#1e3a8a}
.spec-content h3{font-size:14px;font-weight:bold;margin:22px 0 8px;
                 color:#1d4ed8;font-family:Consolas,monospace;
                 background:#f0f6ff;padding:6px 10px;border-radius:4px}
.spec-content p{margin-bottom:8px}
.spec-content ul{margin:6px 0 10px 20px}
.spec-content li{margin-bottom:4px}
.spec-content strong{color:#0f172a}
.spec-content code{background:#f1f5f9;padding:1px 5px;
                   font-family:Consolas,monospace;font-size:12px;color:#be185d}
.spec-content blockquote{border-left:3px solid #94a3b8;padding-left:14px;
                          color:#64748b;font-style:italic;margin:10px 0}
.spec-content hr{border:none;border-top:1px solid #e2e8f0;margin:20px 0}
.spec-content table{width:100%;border-collapse:collapse;font-size:12px;margin:10px 0 16px}
.spec-content th{background:#f0f4f8;padding:8px 12px;border:1px solid #d1d9e6;
                 font-size:11px;text-transform:uppercase;letter-spacing:.4px;text-align:left}
.spec-content td{padding:7px 12px;border:1px solid #e2e8f0;vertical-align:top}
.spec-empty{color:#94a3b8;text-align:center;padding:60px 24px}
.spec-empty .icon{font-size:44px;margin-bottom:12px}
</style>
</head>
<body>

<div class="banner">
  <div>
    <div class="banner-title">&#9670; CERISE &mdash; Reverse Engineering COBOL</div>
    <div class="banner-sub">Sp&eacute;cification champ par champ &mdash; alimentation &amp; r&egrave;gles de gestion</div>
  </div>
</div>

<div class="layout">

  <!-- Sidebar: project list -->
  <div class="sidebar">
    <div class="sidebar-head">
      Projets
      <button style="font-size:11px;background:none;border:none;
                     color:#2563eb;cursor:pointer" onclick="loadProjects()">
        &#8635;
      </button>
    </div>
    <div class="sidebar-body" id="project-list">
      <div style="padding:24px;text-align:center;color:#94a3b8;font-size:12px">
        Chargement&hellip;
      </div>
    </div>
    <div style="padding:10px 12px;border-top:1px solid #eee;
                font-size:11px;color:#94a3b8;line-height:1.5">
      D&eacute;posez vos fichiers dans<br>
      <code style="color:#2563eb">input/&lt;nom_projet&gt;/</code>
    </div>
    <div style="border-top:1px solid #eee;padding-top:8px">
      <button class="btn-test-llm" onclick="testLlm()">
        &#128268; Tester la connexion LLM
      </button>
      <div class="llm-status" id="llm-status"></div>
    </div>
  </div>

  <!-- Main -->
  <div class="main">
    <div class="tabs">
      <div class="tab active" onclick="showTab('setup')">&#9881; Configuration</div>
      <div class="tab"        onclick="showTab('progress')">&#9654; Analyse</div>
      <div class="tab"        onclick="showTab('spec')">&#128196; Sp&eacute;cification</div>
    </div>

    <!-- SETUP tab -->
    <div class="panel active" id="panel-setup">
      <div class="card" id="no-project-card">
        <div class="spec-empty">
          <div class="icon">&#128193;</div>
          <p>S&eacute;lectionnez un projet dans la liste</p>
          <p style="font-size:12px;margin-top:8px">
            ou cr&eacute;ez un dossier dans <code>input/</code>
          </p>
        </div>
      </div>

      <div id="project-setup" style="display:none">
        <div class="card">
          <div class="card-title">Fichiers du projet &mdash; <span id="setup-proj-name">—</span></div>
          <div class="setup-grid" id="setup-grid"></div>
        </div>

        <div class="card" id="fields-preview-card" style="display:none">
          <div class="card-title">Champs cible d&eacute;tect&eacute;s (<span id="fields-count">0</span>)</div>
          <div id="fields-preview" style="display:flex;flex-wrap:wrap;gap:6px"></div>
        </div>

        <div style="display:flex;align-items:center;gap:12px">
          <button class="btn-run" id="btn-run" onclick="startRun()" disabled>
            &#9654; G&eacute;n&eacute;rer la sp&eacute;cification
          </button>
          <span id="run-msg" style="font-size:12px;color:#64748b"></span>
        </div>
      </div>
    </div>

    <!-- PROGRESS tab -->
    <div class="panel" id="panel-progress">
      <div class="card">
        <div class="card-title">Progression</div>
        <div class="progress-label" id="prog-label">En attente&hellip;</div>
        <div class="progress-bar-wrap">
          <div class="progress-bar" id="prog-bar"></div>
        </div>
        <div class="field-list" id="field-list"></div>
      </div>
    </div>

    <!-- SPEC tab -->
    <div class="panel" id="panel-spec">
      <div style="display:flex;gap:8px;align-items:center">
        <select id="spec-select" onchange="loadSpec(this.value)"
                style="padding:6px 10px;border:1px solid #d1d5db;
                       border-radius:4px;font-size:13px;min-width:260px">
          <option value="">— choisir une sp&eacute;cification —</option>
        </select>
        <button onclick="refreshSpecs()"
                style="padding:6px 14px;background:#f8fafc;border:1px solid #d1d5db;
                       border-radius:4px;cursor:pointer;font-size:13px">
          &#8635;
        </button>
      </div>
      <div id="spec-viewer">
        <div class="spec-empty">
          <div class="icon">&#128196;</div>
          <p>La sp&eacute;cification g&eacute;n&eacute;r&eacute;e appara&icirc;tra ici.</p>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
const TABS = ["setup","progress","spec"];
let _currentProject = null;
let _fieldRows = {};
let _runId = null;
let _totalFields = 0;
let _doneFields  = 0;
let _sse = null;

// ── Tabs ──────────────────────────────────────────────────────────────────
function showTab(name){
  TABS.forEach(t => {
    document.getElementById("panel-"+t).classList.toggle("active", t===name);
  });
  document.querySelectorAll(".tab").forEach((el,i)=>
    el.classList.toggle("active", TABS[i]===name));
}

// ── Projects ──────────────────────────────────────────────────────────────
async function loadProjects(){
  const list = document.getElementById("project-list");
  list.innerHTML = '<div style="padding:16px;text-align:center;color:#94a3b8;font-size:12px">Chargement&hellip;</div>';
  const res  = await fetch("/api/projects");
  const data = await res.json();
  if(!data.projects.length){
    list.innerHTML = '<div style="padding:16px;text-align:center;color:#94a3b8;font-size:12px">Aucun projet<br>Cr&eacute;ez un dossier dans input/</div>';
    return;
  }
  list.innerHTML = data.projects.map(p => {
    const chips = [
      chip(p.source,   "COBOL"),
      chip(p.compiled, "LISTING"),
      chip(p.input,    "INPUT"),
      chip(p.target,   "TARGET"),
    ].join("");
    const specChip = p.spec ? '<span class="file-chip chip-spec">SPEC &#10003;</span>' : "";
    return `<div class="project-card ${_currentProject===p.name?'active':''}"
                 onclick="selectProject('${esc(p.name)}')">
      <div class="proj-name">${esc(p.name)}</div>
      <div class="proj-files">${chips}${specChip}</div>
    </div>`;
  }).join("");
}

function chip(present, label){
  return `<span class="file-chip ${present?'chip-ok':'chip-miss'}">${label}</span>`;
}

async function selectProject(name){
  _currentProject = name;
  document.getElementById("no-project-card").style.display="none";
  document.getElementById("project-setup").style.display="";
  document.getElementById("setup-proj-name").textContent = name;
  loadProjects();

  const res  = await fetch("/api/projects");
  const data = await res.json();
  const proj = data.projects.find(p=>p.name===name);
  if(!proj) return;

  // File grid
  const slots = [
    {key:"source",   label:"Source COBOL",        desc:"source.cbl / source.txt"},
    {key:"compiled", label:"Listing compil&eacute;", desc:"compiled.txt"},
    {key:"input",    label:"Structure input",       desc:"input.cpy / input_desc.txt"},
    {key:"target",   label:"Structure cible",       desc:"target.cpy / target_desc.txt"},
  ];
  document.getElementById("setup-grid").innerHTML = slots.map(s =>
    `<div class="file-slot">
       <div class="slot-label">${s.label}</div>
       ${proj[s.key]
         ? `<div class="slot-name">&#10003; ${esc(proj[s.key])}</div>`
         : `<div class="slot-miss">&#10005; manquant</div>`}
       <div class="slot-desc">${s.desc}</div>
     </div>`
  ).join("");

  // Run button
  const btn = document.getElementById("btn-run");
  const msg = document.getElementById("run-msg");
  btn.disabled = !proj.ready;
  msg.textContent = proj.ready ? "" : "Ajoutez les fichiers manquants pour continuer.";

  // Fields preview
  await loadFieldsPreview(name);

  // Auto-load spec if exists
  if(proj.spec){ refreshSpecs(); }
}

async function loadFieldsPreview(name){
  const card    = document.getElementById("fields-preview-card");
  const wrap    = document.getElementById("fields-preview");
  const counter = document.getElementById("fields-count");
  const res  = await fetch("/api/fields/" + encodeURIComponent(name));
  if(!res.ok){ card.style.display="none"; return; }
  const data = await res.json();
  const leaves = (data.fields||[]).filter(f=>!f.is_group);
  counter.textContent = leaves.length;
  if(!leaves.length){ card.style.display="none"; return; }
  card.style.display="";
  wrap.innerHTML = leaves.map(f =>
    `<span style="background:#f0f6ff;color:#1d4ed8;padding:3px 9px;
                  border-radius:12px;font-size:11px;font-family:Consolas,monospace">
      ${esc(f.name)}
     </span>`
  ).join("");
}

// ── Run pipeline ──────────────────────────────────────────────────────────
async function startRun(){
  if(!_currentProject) return;
  const btn = document.getElementById("btn-run");
  btn.disabled = true;

  const res  = await fetch("/api/run/" + encodeURIComponent(_currentProject),
                           {method:"POST"});
  const data = await res.json();
  if(!res.ok){
    alert("Erreur : " + (data.error||"?"));
    btn.disabled = false;
    return;
  }
  _runId = data.run_id;
  _fieldRows = {};
  _doneFields  = 0;
  _totalFields = 0;
  document.getElementById("field-list").innerHTML = "";
  document.getElementById("prog-bar").style.width = "0";
  document.getElementById("prog-label").textContent = "Initialisation…";
  showTab("progress");
  connectSSE(_runId);
}

// ── SSE ───────────────────────────────────────────────────────────────────
function connectSSE(runId){
  if(_sse) _sse.close();
  _sse = new EventSource("/api/events/" + runId);
  _sse.onmessage = e => {
    const ev = JSON.parse(e.data);
    if(ev.type === "start"){
      _totalFields = ev.total;
      document.getElementById("prog-label").textContent =
        "0 / " + ev.total + " champs traités";
      // Pre-populate field list
      const list = document.getElementById("field-list");
      list.innerHTML = "";
      (ev.fields||[]).forEach((name,i) => {
        const row = document.createElement("div");
        row.className = "field-row";
        row.id = "fr-" + name;
        row.innerHTML =
          `<div class="field-status st-pending" id="fst-${esc(name)}">&#8203;</div>`+
          `<div class="field-name-cell">${esc(name)}</div>`+
          `<div class="field-pic-cell" id="fpic-${esc(name)}"></div>`;
        list.appendChild(row);
        _fieldRows[name] = row;
      });
    }
    else if(ev.type === "field_start"){
      const row = _fieldRows[ev.field];
      if(row){
        row.className = "field-row running";
        document.getElementById("fst-"+ev.field).className="field-status st-running";
        document.getElementById("fst-"+ev.field).textContent="&#9654;";
        row.scrollIntoView({block:"nearest"});
      }
      document.getElementById("prog-label").textContent =
        ev.index + " / " + ev.total + " — " + ev.field;
    }
    else if(ev.type === "field_done"){
      _doneFields++;
      const row = _fieldRows[ev.field];
      if(row){
        row.className = "field-row " + (ev.found ? "done-ok" : "done-miss");
        const st = document.getElementById("fst-"+ev.field);
        st.className = "field-status " + (ev.found ? "st-ok" : "st-miss");
        st.textContent = ev.found ? "&#10003;" : "&#10007;";
      }
      const pct = _totalFields ? (_doneFields/_totalFields*100).toFixed(0) : 0;
      document.getElementById("prog-bar").style.width = pct + "%";
      document.getElementById("prog-label").textContent =
        _doneFields + " / " + _totalFields + " champs traités (" + pct + "%)";
    }
    else if(ev.type === "done"){
      _sse.close();
      document.getElementById("btn-run").disabled = false;
      document.getElementById("prog-label").textContent =
        "&#10003; Terminé — " + ev.found + "/" + ev.total + " champs alimentés";
      refreshSpecs().then(() => {
        const sel = document.getElementById("spec-select");
        const specName = _currentProject.toUpperCase() + "_SPEC.md";
        for(let o of sel.options){
          if(o.value === specName){ sel.value = specName; break; }
        }
        loadSpec(sel.value);
        showTab("spec");
      });
      loadProjects();
    }
    else if(ev.type === "error"){
      document.getElementById("prog-label").textContent = "Erreur : " + ev.message;
      document.getElementById("btn-run").disabled = false;
      if(_sse) _sse.close();
    }
  };
}

// ── Spec viewer ───────────────────────────────────────────────────────────
async function refreshSpecs(){
  const res  = await fetch("/api/specs");
  const data = await res.json();
  const sel  = document.getElementById("spec-select");
  const cur  = sel.value;
  sel.innerHTML = '<option value="">— choisir une sp&eacute;cification —</option>' +
    (data.specs||[]).map(s=>`<option value="${esc(s)}">${esc(s)}</option>`).join("");
  if(cur) sel.value = cur;
}

async function loadSpec(name){
  const viewer = document.getElementById("spec-viewer");
  if(!name){
    viewer.innerHTML = '<div class="spec-empty"><div class="icon">&#128196;</div><p>S&eacute;lectionnez une sp&eacute;cification.</p></div>';
    return;
  }
  const res = await fetch("/api/spec/"+encodeURIComponent(name));
  if(!res.ok){ viewer.innerHTML='<div class="spec-empty"><p>Introuvable.</p></div>'; return; }
  const md = await res.text();
  viewer.innerHTML = '<div class="spec-content">' + renderMd(md) + '</div>';
}

function renderMd(md){
  return md
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/^# (.+)$/gm,"<h1>$1</h1>")
    .replace(/^## (.+)$/gm,"<h2>$1</h2>")
    .replace(/^### (.+)$/gm,"<h3>$1</h3>")
    .replace(/^> (.+)$/gm,"<blockquote>$1</blockquote>")
    .replace(/^---$/gm,"<hr>")
    .replace(/[*][*](.+?)[*][*]/g,"<strong>$1</strong>")
    .replace(/`([^`]+)`/g,"<code>$1</code>")
    .replace(/^- (.+)$/gm,"<li>$1</li>")
    .replace(/(<li>[^<]+<\\/li>\\n?)+/g, s=>"<ul>"+s+"</ul>")
    .replace(/\\n/g,"<br>");
}

function esc(s){
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;")
                  .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

// ── LLM connection test ────────────────────────────────────────────────────
async function testLlm(){
  const box = document.getElementById("llm-status");
  box.className = "llm-status";
  box.textContent = "Test en cours…";
  box.style.display = "block";
  try {
    const res  = await fetch("/api/test-llm");
    const data = await res.json();
    if(data.ok){
      box.className = "llm-status llm-ok";
      box.innerHTML =
        "&#10003; Connexion OK<br>" +
        "<b>Modèle :</b> " + esc(data.model) + "<br>" +
        "<b>URL :</b> " + esc(data.base_url) + "<br>" +
        "<b>Réponse :</b> " + esc(data.reply) + "<br>" +
        "<b>Latence :</b> " + data.ms + " ms";
    } else {
      box.className = "llm-status llm-err";
      box.innerHTML =
        "&#10007; Erreur de connexion<br>" +
        "<b>URL :</b> " + esc(data.base_url) + "<br>" +
        "<b>Modèle :</b> " + esc(data.model) + "<br>" +
        "<b>Détail :</b> " + esc(data.error);
    }
  } catch(e) {
    box.className = "llm-status llm-err";
    box.textContent = "Erreur réseau : " + e.message;
  }
}

window.addEventListener("DOMContentLoaded", () => {
  loadProjects();
  refreshSpecs();
});
</script>
</body>
</html>
"""

# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    INPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)
    log("info", "CERISE dashboard on http://127.0.0.1:5001")
    app.run(host="0.0.0.0", port=5001, debug=False)
