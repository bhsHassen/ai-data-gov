"""
AI Data Gov — Live Execution Dashboard
Web UI that runs the pipeline and streams every step in real time.

Usage:
  python dashboard.py
  python dashboard.py --port 8080

Then open: http://localhost:5000
"""
from __future__ import annotations

import argparse
import json
import queue
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import markdown
from flask import Flask, Response, abort, render_template_string, request, stream_with_context

from src.ai_data_gov import console as _console

OUTPUT_DIR = Path("output")
app = Flask(__name__)

# ── Active runs (run_id → Queue) ────────────────────────────────────────────
_runs: dict[str, queue.Queue] = {}
_runs_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────────
#  Pipeline runner
# ─────────────────────────────────────────────────────────────────────────────

def _run_pipeline(run_id: str, flow_name: str, location: str | None,
                  self_review_enabled: bool) -> None:
    """Runs the LangGraph pipeline in a background thread."""
    # Lazy import so the module loads fast
    from src.ai_data_gov.graph import build_graph

    q = _runs[run_id]

    try:
        _console.attach_queue(q)

        initial_state = {
            "flow_name":           flow_name,
            "location":            location or None,
            "source_files_count":  0,
            "ddl_files_count":     0,
            "doc_files_count":     0,
            "raw_context":         "",
            "spec_drafts":         {},
            "spec_draft":          "",
            "validation_ok":       False,
            "validation_errors":   [],
            "retry_count":         0,
            "self_review_enabled": self_review_enabled,
            "output_path":         None,
        }

        graph = build_graph()
        graph.invoke(initial_state)

    except Exception as exc:  # noqa: BLE001
        _console.emit_event({"type": "error", "message": str(exc)})

    finally:
        _console.detach_queue()
        q.put(None)   # sentinel → close SSE stream


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers (shared with preview.py)
# ─────────────────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _build_toc(md_text: str) -> list[dict]:
    return [
        {"label": line[3:].strip(), "anchor": _slugify(line[3:].strip())}
        for line in md_text.splitlines()
        if line.startswith("## ")
    ]


def _md_to_html(md_text: str) -> str:
    html = markdown.markdown(md_text, extensions=["tables", "fenced_code", "nl2br"])
    html = re.sub(
        r"<h(\d)>(.*?)</h\1>",
        lambda m: f'<h{m.group(1)} id="{_slugify(m.group(2))}">{m.group(2)}</h{m.group(1)}>',
        html, flags=re.DOTALL,
    )
    return html


def _spec_title(filename: str) -> str:
    return filename.replace("FLOW_", "").replace("_SPEC.md", "").replace("_", " ").title()


def _list_specs() -> list[dict]:
    if not OUTPUT_DIR.exists():
        return []
    specs = []
    for f in sorted(OUTPUT_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        st = f.stat()
        specs.append({
            "filename": f.name,
            "title":    _spec_title(f.name),
            "size":     round(st.st_size / 1024, 1),
            "mtime":    datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    return specs


def _load_md(filename: str) -> str:
    if ".." in filename or not filename.endswith(".md"):
        abort(400)
    path = OUTPUT_DIR / filename
    if not path.exists():
        abort(404)
    return path.read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
#  HTML  ·  shared CSS
# ─────────────────────────────────────────────────────────────────────────────

_CONTENT_CSS = """
    h1{font-size:26px;font-weight:700;border-bottom:2px solid #334155;
       padding-bottom:14px;margin-bottom:28px;color:#e2e8f0}
    h2{font-size:18px;font-weight:700;margin:36px 0 12px;padding-left:10px;
       border-left:4px solid #0052cc;color:#e2e8f0}
    h3{font-size:14px;font-weight:600;margin:20px 0 8px;color:#cbd5e1}
    p{font-size:14px;line-height:1.7;color:#cbd5e1;margin-bottom:12px}
    ul,ol{font-size:14px;line-height:1.7;margin:8px 0 12px 22px;color:#cbd5e1}
    table{width:100%;border-collapse:collapse;font-size:13px;margin:14px 0 20px}
    thead th{background:#1e3a5f;color:#93c5fd;font-weight:600;text-align:left;
             padding:9px 12px;border:1px solid #334155;white-space:nowrap}
    tbody td{padding:8px 12px;border:1px solid #334155;vertical-align:top;
             line-height:1.5;color:#cbd5e1}
    tbody tr:nth-child(even){background:#1a2744}
    tbody tr:hover{background:#1e3a5f}
    blockquote{border-left:3px solid #eab308;background:#2d2a1a;
               padding:8px 14px;margin:4px 0 12px;border-radius:0 4px 4px 0;
               font-size:13px;color:#fde68a}
    code{background:#1e293b;border:1px solid #334155;border-radius:3px;
         font-family:"SFMono-Regular",Consolas,monospace;font-size:12px;
         padding:1px 5px;color:#7dd3fc}
    hr{border:none;border-top:1px solid #334155;margin:28px 0}
    td{word-break:break-word}
"""

_PRINT_CSS = """
    @media print{
      .topbar,.sidebar,.btn-pdf,.print-banner{display:none!important}
      .layout{display:block}
      body{background:#fff;color:#000;padding:0}
      .content{padding:0;max-width:100%}
      *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
      @page{size:A4;margin:18mm 15mm}
      h1{color:#000;border-bottom:2pt solid #ddd;font-size:18pt}
      h2{page-break-before:always;break-before:page;color:#000;font-size:13pt;
         border-left:4px solid #0052cc;padding-left:8px;margin-top:0}
      h2:first-of-type{page-break-before:avoid;break-before:avoid}
      table{page-break-inside:avoid;font-size:9pt}
      thead th{background:#eee!important;color:#000!important}
      tbody td{color:#000!important;border-color:#ccc!important}
      tbody tr:nth-child(even){background:#f9f9f9!important}
      tbody tr:hover{background:transparent!important}
      blockquote{background:#fffae6!important;color:#333!important;
                 border-left:3px solid #ffab00!important}
      p,li{color:#000}
      h3{color:#000}
    }
"""


# ─────────────────────────────────────────────────────────────────────────────
#  Dashboard page
# ─────────────────────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Data Gov — Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
     background:#0f172a;color:#e2e8f0;min-height:100vh}

/* ── Topbar ── */
.topbar{background:#0a1628;border-bottom:1px solid #1e3a5f;
        padding:14px 32px;display:flex;align-items:center;justify-content:space-between}
.topbar-brand{display:flex;align-items:center;gap:12px}
.topbar-brand svg{width:28px;height:28px}
.topbar-brand span{font-size:17px;font-weight:700;letter-spacing:-.01em}
.topbar-brand small{font-size:12px;color:#64748b;margin-left:4px}
.topbar-nav a{color:#94a3b8;font-size:13px;text-decoration:none;
              padding:6px 14px;border-radius:4px;transition:all .15s}
.topbar-nav a:hover{color:#e2e8f0;background:#1e293b}

/* ── Layout ── */
.page{max-width:1100px;margin:0 auto;padding:32px 24px 60px}

/* ── Form card ── */
.form-card{background:#1e293b;border:1px solid #334155;border-radius:10px;
           padding:28px 32px;margin-bottom:32px}
.form-card h2{font-size:16px;font-weight:600;color:#94a3b8;
              text-transform:uppercase;letter-spacing:.06em;margin-bottom:20px}
.form-row{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-end}
.form-group{display:flex;flex-direction:column;gap:6px;flex:1;min-width:180px}
.form-group label{font-size:12px;font-weight:600;color:#64748b;text-transform:uppercase;
                  letter-spacing:.05em}
.form-group input{background:#0f172a;border:1px solid #334155;color:#e2e8f0;
                  border-radius:6px;padding:9px 14px;font-size:14px;outline:none;
                  transition:border-color .15s;font-family:inherit}
.form-group input:focus{border-color:#0052cc}
.form-group input::placeholder{color:#475569}

.toggle-row{display:flex;align-items:center;gap:10px;padding-bottom:2px}
.toggle-label{font-size:13px;color:#94a3b8}
.toggle{position:relative;width:40px;height:22px;cursor:pointer}
.toggle input{opacity:0;width:0;height:0}
.toggle-track{position:absolute;inset:0;background:#334155;border-radius:11px;
              transition:background .2s}
.toggle input:checked~.toggle-track{background:#0052cc}
.toggle-thumb{position:absolute;top:3px;left:3px;width:16px;height:16px;
              background:#fff;border-radius:50%;transition:transform .2s}
.toggle input:checked~.toggle-thumb{transform:translateX(18px)}

.btn-run{background:#0052cc;color:#fff;border:none;border-radius:6px;
         padding:10px 28px;font-size:14px;font-weight:600;cursor:pointer;
         transition:background .15s;white-space:nowrap;font-family:inherit}
.btn-run:hover{background:#0065ff}
.btn-run:disabled{background:#1e3a5f;color:#475569;cursor:not-allowed}

/* ── Pipeline ── */
.pipeline-card{background:#1e293b;border:1px solid #334155;border-radius:10px;
               padding:28px 32px;margin-bottom:24px}
.pipeline-title{font-size:12px;font-weight:600;color:#64748b;text-transform:uppercase;
                letter-spacing:.06em;margin-bottom:24px}
.pipeline-row{display:flex;align-items:center;gap:0;overflow-x:auto;padding-bottom:4px}

/* ── Node ── */
.p-node{display:flex;flex-direction:column;align-items:center;gap:6px;
        min-width:110px;padding:16px 8px;border-radius:8px;
        border:1px solid #334155;background:#0f172a;transition:all .3s;
        position:relative;cursor:default}
.p-node .pn-icon{font-size:22px;line-height:1}
.p-node .pn-name{font-size:12px;font-weight:700;color:#94a3b8;text-align:center;
                 text-transform:uppercase;letter-spacing:.04em}
.p-node .pn-detail{font-size:10px;color:#475569;text-align:center;
                   min-height:14px;max-width:100px;line-height:1.3;
                   word-break:break-word}
.p-node .pn-status{width:8px;height:8px;border-radius:50%;background:#334155;
                   position:absolute;top:8px;right:8px}

/* States */
.p-node.running{border-color:#0052cc;background:#0d1f3c;
                box-shadow:0 0 16px rgba(0,82,204,.35)}
.p-node.running .pn-name{color:#60a5fa}
.p-node.running .pn-status{background:#0052cc;
  animation:pulse-dot 1.2s ease-in-out infinite}
.p-node.done{border-color:#16a34a;background:#0d1f14}
.p-node.done .pn-name{color:#4ade80}
.p-node.done .pn-status{background:#22c55e}
.p-node.skipped{opacity:.4}
.p-node.skipped .pn-name{text-decoration:line-through}
.p-node.error{border-color:#dc2626;background:#1f0d0d}
.p-node.error .pn-name{color:#f87171}
.p-node.error .pn-status{background:#ef4444}

@keyframes pulse-dot{
  0%,100%{box-shadow:0 0 0 0 rgba(0,82,204,.7)}
  50%{box-shadow:0 0 0 5px rgba(0,82,204,0)}
}

/* ── Arrow ── */
.p-arrow{flex-shrink:0;width:36px;display:flex;align-items:center;
         justify-content:center;position:relative}
.p-arrow::before{content:"";display:block;height:2px;width:100%;
                 background:#334155;transition:background .4s}
.p-arrow::after{content:"▶";font-size:9px;color:#334155;
                position:absolute;right:-1px;transition:color .4s}
.p-arrow.active::before{background:#0052cc}
.p-arrow.active::after{color:#0052cc}
.p-arrow.done::before{background:#16a34a}
.p-arrow.done::after{color:#16a34a}

/* ── Timer ── */
.timer-bar{display:flex;align-items:center;gap:12px;margin-top:16px}
.timer{font-size:12px;color:#475569;font-variant-numeric:tabular-nums}
.timer span{color:#94a3b8;font-weight:600}
.stage-label{font-size:12px;color:#0052cc;font-weight:500}

/* ── Log terminal ── */
.log-card{background:#0a0f1a;border:1px solid #1e293b;border-radius:10px;
          padding:0;overflow:hidden;margin-bottom:24px}
.log-header{background:#1e293b;padding:10px 20px;display:flex;
            align-items:center;justify-content:space-between}
.log-header span{font-size:12px;font-weight:600;color:#64748b;
                 text-transform:uppercase;letter-spacing:.06em}
.log-header .dot{width:10px;height:10px;border-radius:50%;background:#334155}
.log-header .dot.live{background:#22c55e;animation:blink 1s step-start infinite}
@keyframes blink{50%{opacity:0}}
.log-body{height:240px;overflow-y:auto;padding:14px 20px;
          font-family:"SFMono-Regular",Consolas,monospace;font-size:12px;
          line-height:1.7;scroll-behavior:smooth}
.log-line{display:flex;gap:10px;margin-bottom:1px}
.log-agent{font-weight:700;min-width:78px;text-align:right;flex-shrink:0}
.log-msg{color:#94a3b8;word-break:break-word}
.agent-collector{color:#60a5fa}
.agent-analyst{color:#34d399}
.agent-judge{color:#fbbf24}
.agent-validator{color:#a78bfa}
.agent-writer{color:#f472b6}
.agent-router{color:#6b7280}
.agent-error{color:#f87171}

/* ── Result banner ── */
.result-card{border-radius:10px;padding:24px 32px;
             display:flex;align-items:center;justify-content:space-between;
             gap:16px;flex-wrap:wrap;margin-bottom:24px;display:none}
.result-card.success{background:#0d2818;border:1px solid #16a34a}
.result-card.partial{background:#2a1f0a;border:1px solid #d97706}
.result-info h3{font-size:16px;font-weight:700;margin-bottom:4px}
.result-card.success .result-info h3{color:#4ade80}
.result-card.partial .result-info h3{color:#fbbf24}
.result-info p{font-size:13px;color:#64748b}
.result-actions{display:flex;gap:10px}
.btn-view{background:#0052cc;color:#fff;text-decoration:none;border-radius:6px;
          padding:9px 20px;font-size:13px;font-weight:600;transition:background .15s}
.btn-view:hover{background:#0065ff}
.btn-pdf{background:transparent;color:#7c3aed;border:1px solid #7c3aed;
         text-decoration:none;border-radius:6px;padding:9px 20px;
         font-size:13px;font-weight:600;transition:all .15s}
.btn-pdf:hover{background:#7c3aed;color:#fff}

/* ── Specs list ── */
.specs-card{background:#1e293b;border:1px solid #334155;border-radius:10px;
            padding:28px 32px}
.specs-title{font-size:12px;font-weight:600;color:#64748b;text-transform:uppercase;
             letter-spacing:.06em;margin-bottom:16px}
.spec-row{display:flex;align-items:center;justify-content:space-between;
          padding:12px 0;border-bottom:1px solid #1e293b;gap:12px}
.spec-row:last-child{border-bottom:none}
.spec-name{font-size:14px;font-weight:600;color:#e2e8f0}
.spec-meta{font-size:12px;color:#475569;margin-top:2px}
.spec-actions{display:flex;gap:8px;flex-shrink:0}
.spec-actions a{font-size:12px;text-decoration:none;padding:5px 12px;
                border-radius:4px;font-weight:500;transition:all .15s}
.spec-actions .a-open{color:#60a5fa;border:1px solid #1e3a5f}
.spec-actions .a-open:hover{background:#1e3a5f}
.spec-actions .a-pdf{color:#a78bfa;border:1px solid #2d1f5e}
.spec-actions .a-pdf:hover{background:#2d1f5e}
.empty-specs{color:#475569;font-size:14px;text-align:center;padding:20px 0}
</style>
</head>
<body>

<div class="topbar">
  <div class="topbar-brand">
    <svg viewBox="0 0 32 32" fill="none"><rect width="32" height="32" rx="6" fill="#0052cc"/>
      <path d="M8 22l4-8 4 5 4-9 4 12" stroke="#fff" stroke-width="2.2"
            stroke-linecap="round" stroke-linejoin="round"/></svg>
    <span>AI Data Gov</span>
    <small>Flow Specification Generator</small>
  </div>
  <nav class="topbar-nav">
    <a href="/">Dashboard</a>
  </nav>
</div>

<div class="page">

  <!-- Form -->
  <div class="form-card">
    <h2>New Run</h2>
    <div class="form-row">
      <div class="form-group">
        <label>Flow Name</label>
        <input type="text" id="inp-flow" placeholder="e.g. TIERS_LEI" autocomplete="off">
      </div>
      <div class="form-group">
        <label>Location <span style="color:#475569;font-weight:400">(optional)</span></label>
        <input type="text" id="inp-location" placeholder="e.g. Sydney" autocomplete="off">
      </div>
      <div class="form-group" style="flex:0">
        <label>Self-Review</label>
        <div class="toggle-row">
          <label class="toggle">
            <input type="checkbox" id="chk-selfreview" checked>
            <div class="toggle-track"></div>
            <div class="toggle-thumb"></div>
          </label>
          <span class="toggle-label" id="sr-label">Enabled</span>
        </div>
      </div>
      <div class="form-group" style="flex:0">
        <label>&nbsp;</label>
        <button class="btn-run" id="btn-run" onclick="startRun()">▶ Run Pipeline</button>
      </div>
    </div>
  </div>

  <!-- Pipeline visualization -->
  <div class="pipeline-card" id="pipeline-card" style="display:none">
    <div class="pipeline-title">Pipeline Execution</div>
    <div class="pipeline-row">
      <div class="p-node pending" id="pn-collector">
        <div class="pn-status"></div>
        <div class="pn-icon">📂</div>
        <div class="pn-name">Collector</div>
        <div class="pn-detail"></div>
      </div>
      <div class="p-arrow" id="pa-analyst"></div>
      <div class="p-node pending" id="pn-analyst">
        <div class="pn-status"></div>
        <div class="pn-icon">⚡</div>
        <div class="pn-name">Analysts</div>
        <div class="pn-detail"></div>
      </div>
      <div class="p-arrow" id="pa-judge"></div>
      <div class="p-node pending" id="pn-judge">
        <div class="pn-status"></div>
        <div class="pn-icon">⚖️</div>
        <div class="pn-name">Judge</div>
        <div class="pn-detail"></div>
      </div>
      <div class="p-arrow" id="pa-self_review"></div>
      <div class="p-node pending" id="pn-self_review">
        <div class="pn-status"></div>
        <div class="pn-icon">🔍</div>
        <div class="pn-name">Self-Review</div>
        <div class="pn-detail"></div>
      </div>
      <div class="p-arrow" id="pa-validator"></div>
      <div class="p-node pending" id="pn-validator">
        <div class="pn-status"></div>
        <div class="pn-icon">✅</div>
        <div class="pn-name">Validator</div>
        <div class="pn-detail"></div>
      </div>
      <div class="p-arrow" id="pa-writer"></div>
      <div class="p-node pending" id="pn-writer">
        <div class="pn-status"></div>
        <div class="pn-icon">📄</div>
        <div class="pn-name">Writer</div>
        <div class="pn-detail"></div>
      </div>
    </div>
    <div class="timer-bar">
      <div class="timer">Elapsed: <span id="elapsed">0s</span></div>
      <div class="stage-label" id="stage-label"></div>
    </div>
  </div>

  <!-- Live log -->
  <div class="log-card" id="log-card" style="display:none">
    <div class="log-header">
      <span>Live Output</span>
      <div class="dot" id="log-dot"></div>
    </div>
    <div class="log-body" id="log-body"></div>
  </div>

  <!-- Result -->
  <div class="result-card" id="result-card">
    <div class="result-info">
      <h3 id="result-title">Spec generated</h3>
      <p id="result-detail"></p>
    </div>
    <div class="result-actions">
      <a href="#" class="btn-view" id="btn-view" target="_blank">View Spec →</a>
      <a href="#" class="btn-pdf"  id="btn-pdf"  target="_blank">⬇ Export PDF</a>
    </div>
  </div>

  <!-- Previous specs -->
  <div class="specs-card">
    <div class="specs-title">Previous Specifications</div>
    <div id="specs-list">
      {% if specs %}
        {% for s in specs %}
        <div class="spec-row">
          <div>
            <div class="spec-name">{{ s.title }}</div>
            <div class="spec-meta">{{ s.size }} KB · {{ s.mtime }}</div>
          </div>
          <div class="spec-actions">
            <a href="/spec/{{ s.filename }}" class="a-open" target="_blank">Open →</a>
            <a href="/print/{{ s.filename }}" class="a-pdf" target="_blank">⬇ PDF</a>
          </div>
        </div>
        {% endfor %}
      {% else %}
        <div class="empty-specs">No specs yet — run the pipeline above.</div>
      {% endif %}
    </div>
  </div>

</div><!-- /page -->

<script>
const STAGE_ORDER = ["collector","analyst","judge","self_review","validator","writer"];
let _evtSource = null;
let _startTime = null;
let _timerInterval = null;
let _lastStage = null;

// Toggle label
document.getElementById("chk-selfreview").addEventListener("change", function(){
  document.getElementById("sr-label").textContent = this.checked ? "Enabled" : "Disabled";
});

function setNode(stage, state, detail){
  const node = document.getElementById("pn-"+stage);
  if(!node) return;
  node.className = "p-node " + state;
  if(detail !== undefined){
    node.querySelector(".pn-detail").textContent = detail;
  }
  // Activate arrow pointing TO this node
  const prev = STAGE_ORDER[STAGE_ORDER.indexOf(stage)-1];
  if(prev){
    const arr = document.getElementById("pa-"+stage);
    if(arr) arr.className = "p-arrow " + (state==="done" ? "done" : "active");
  }
}

function setStageLabel(text){ document.getElementById("stage-label").textContent = text; }

function addLog(agent, message){
  const body = document.getElementById("log-body");
  const line = document.createElement("div");
  line.className = "log-line";
  line.innerHTML = `<span class="log-agent agent-${agent}">[${agent.toUpperCase()}]</span>`
                 + `<span class="log-msg">${escHtml(message)}</span>`;
  body.appendChild(line);
  body.scrollTop = body.scrollHeight;
}

function escHtml(s){
  return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function startTimer(){
  _startTime = Date.now();
  _timerInterval = setInterval(()=>{
    const s = Math.round((Date.now()-_startTime)/1000);
    document.getElementById("elapsed").textContent = s+"s";
  }, 500);
}

function stopTimer(){ clearInterval(_timerInterval); }

function showResult(outputPath, ok){
  stopTimer();
  document.getElementById("log-dot").classList.remove("live");

  const card   = document.getElementById("result-card");
  const title  = document.getElementById("result-title");
  const detail = document.getElementById("result-detail");
  const btnV   = document.getElementById("btn-view");
  const btnP   = document.getElementById("btn-pdf");

  const elapsed = Math.round((Date.now()-_startTime)/1000);
  const filename = outputPath.split(/[\\\\/]/).pop();
  const specUrl  = "/spec/"+filename;
  const pdfUrl   = "/print/"+filename;

  card.className = "result-card " + (ok ? "success" : "partial");
  title.textContent  = ok ? "✅ Specification complete" : "⚠️ Partial specification";
  detail.textContent = filename + "  ·  generated in " + elapsed + "s";
  btnV.href = specUrl;
  btnP.href = pdfUrl;
  card.style.display = "flex";

  document.getElementById("btn-run").disabled = false;
}

function startRun(){
  const flow     = document.getElementById("inp-flow").value.trim().toUpperCase();
  const location = document.getElementById("inp-location").value.trim();
  const selfRev  = document.getElementById("chk-selfreview").checked;

  if(!flow){ document.getElementById("inp-flow").focus(); return; }

  // Reset UI
  document.getElementById("result-card").style.display = "none";
  document.getElementById("log-body").innerHTML = "";
  STAGE_ORDER.forEach(s => {
    setNode(s, "pending", "");
    const arr = document.getElementById("pa-"+s);
    if(arr) arr.className = "p-arrow";
  });
  document.getElementById("elapsed").textContent = "0s";
  setStageLabel("");

  // Show cards
  document.getElementById("pipeline-card").style.display = "block";
  document.getElementById("log-card").style.display = "block";
  document.getElementById("log-dot").classList.add("live");
  document.getElementById("btn-run").disabled = true;

  // Handle skipped self-review
  if(!selfRev) setNode("self_review","skipped","disabled");

  // POST → get run_id
  fetch("/api/run", {
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body: JSON.stringify({flow_name:flow, location:location||null, self_review_enabled:selfRev})
  })
  .then(r=>r.json())
  .then(data=>{
    startTimer();
    connectSSE(data.run_id);
  })
  .catch(err=>{
    addLog("error","Failed to start run: "+err);
    document.getElementById("btn-run").disabled = false;
  });
}

function connectSSE(runId){
  if(_evtSource) _evtSource.close();
  _evtSource = new EventSource("/api/events/"+runId);

  _evtSource.onmessage = function(e){
    const ev = JSON.parse(e.data);

    if(ev.type === "heartbeat") return;

    if(ev.type === "log"){
      addLog(ev.agent, ev.message);
      return;
    }

    if(ev.type === "stage_start"){
      setNode(ev.stage, "running", ev.detail||"");
      setStageLabel(ev.stage.replace("_"," ").toUpperCase());
      return;
    }

    if(ev.type === "stage_done"){
      setNode(ev.stage, "done", ev.detail||"");
      return;
    }

    if(ev.type === "retry"){
      addLog("router", "Validation failed — retrying ("+ev.retry_count+"/"+ev.max+")");
      // Reset analyst → writer nodes
      ["analyst","judge","self_review","validator","writer"].forEach(s=>{
        setNode(s,"pending","");
        const arr=document.getElementById("pa-"+s);
        if(arr) arr.className="p-arrow";
      });
      return;
    }

    if(ev.type === "pipeline_complete"){
      showResult(ev.output_path, ev.validation_ok);
      _evtSource.close();
      return;
    }

    if(ev.type === "error"){
      addLog("error", ev.message);
      stopTimer();
      document.getElementById("log-dot").classList.remove("live");
      document.getElementById("btn-run").disabled = false;
      _evtSource.close();
      return;
    }

    if(ev.type === "done"){
      _evtSource.close();
      return;
    }
  };

  _evtSource.onerror = function(){
    addLog("error", "Connection lost.");
    stopTimer();
    document.getElementById("btn-run").disabled = false;
    _evtSource.close();
  };
}
</script>
</body>
</html>
"""


# ─────────────────────────────────────────────────────────────────────────────
#  Spec viewer (same CSS as preview.py, dark theme)
# ─────────────────────────────────────────────────────────────────────────────

SPEC_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }}</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
     background:#0f172a;color:#e2e8f0}
.topbar{background:#0a1628;border-bottom:1px solid #1e3a5f;padding:12px 28px;
        display:flex;align-items:center;justify-content:space-between}
.topbar-left{display:flex;align-items:center;gap:10px}
.topbar-left svg{width:22px;height:22px}
.topbar-left span{font-size:15px;font-weight:600}
.topbar-right{display:flex;gap:8px}
.topbar-right a,.btn-pdf{color:#e2e8f0;font-size:12px;text-decoration:none;
  border:1px solid #334155;border-radius:4px;padding:5px 14px;
  background:transparent;cursor:pointer;font-family:inherit;transition:all .15s}
.topbar-right a:hover,.btn-pdf:hover{background:#1e293b}
.layout{display:flex;min-height:calc(100vh - 48px)}
.sidebar{width:220px;flex-shrink:0;background:#0a1628;border-right:1px solid #1e3a5f;
         padding:20px 14px;position:sticky;top:0;height:calc(100vh - 48px);overflow-y:auto}
.sidebar h3{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;
            color:#475569;margin-bottom:10px}
.sidebar ul{list-style:none}
.sidebar li{margin-bottom:3px}
.sidebar a{display:block;font-size:12px;color:#94a3b8;text-decoration:none;
           padding:5px 10px;border-radius:4px;transition:all .15s}
.sidebar a:hover{background:#1e293b;color:#60a5fa}
.content{flex:1;padding:36px 52px 60px;max-width:1020px}
""" + _CONTENT_CSS + _PRINT_CSS + """
</style>
</head>
<body>
<div class="topbar">
  <div class="topbar-left">
    <svg viewBox="0 0 32 32" fill="none"><rect width="32" height="32" rx="6" fill="#0052cc"/>
      <path d="M8 22l4-8 4 5 4-9 4 12" stroke="#fff" stroke-width="2.2"
            stroke-linecap="round" stroke-linejoin="round"/></svg>
    <span>{{ title }}</span>
  </div>
  <div class="topbar-right">
    <button class="btn-pdf" onclick="window.open('/print/{{ filename }}','_blank')">
      ⬇ Export PDF
    </button>
    <a href="/">← Dashboard</a>
  </div>
</div>
<div class="layout">
  <nav class="sidebar">
    <h3>Contents</h3>
    <ul>{% for item in toc %}
      <li><a href="#{{ item.anchor }}">{{ item.label }}</a></li>
    {% endfor %}</ul>
  </nav>
  <div class="content">{{ body | safe }}</div>
</div>
</body></html>
"""

PRINT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><title>{{ title }}</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
     background:#0f172a;color:#e2e8f0;padding:32px 48px}
.print-banner{background:#0052cc;color:#fff;padding:12px 20px;
              margin:-32px -48px 32px;display:flex;align-items:center;
              justify-content:space-between;font-size:13px}
.print-banner strong{font-size:14px}
.print-banner .actions{display:flex;gap:10px;align-items:center}
.print-banner button{background:#fff;color:#0052cc;border:none;border-radius:3px;
                     padding:6px 16px;font-size:13px;font-weight:600;cursor:pointer}
.print-banner a{color:rgba(255,255,255,.75);font-size:12px;text-decoration:none}
""" + _CONTENT_CSS + """
@media print{
  .print-banner{display:none!important}
  body{background:#fff;color:#000;padding:0}
  *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
  @page{size:A4;margin:18mm 15mm 18mm 15mm}
  h1{color:#000;border-bottom:2pt solid #ddd;font-size:18pt}
  h2{page-break-before:always;break-before:page;color:#000;font-size:13pt;
     border-left:4px solid #0052cc;padding-left:8px;margin-top:0}
  h2:first-of-type{page-break-before:avoid;break-before:avoid}
  table{page-break-inside:avoid;font-size:9pt}
  thead th{background:#eee!important;color:#000!important}
  tbody td{color:#000!important;border-color:#ccc!important}
  tbody tr:nth-child(even){background:#f9f9f9!important}
  tbody tr:hover{background:transparent!important}
  blockquote{background:#fffae6!important;color:#333!important;
             border-left:3px solid #ffab00!important}
  p,li,h3{color:#000}
}
</style>
</head>
<body>
<div class="print-banner">
  <div><strong>⬇ Export as PDF</strong>&nbsp;—&nbsp;
    File › Print · Destination: <em>Save as PDF</em> · A4 · Enable "Background graphics"
  </div>
  <div class="actions">
    <button onclick="window.print()">🖨 Print / Save as PDF</button>
    <a href="/spec/{{ filename }}">← Back to viewer</a>
  </div>
</div>
{{ body | safe }}
<script>
  window.addEventListener('load',()=>setTimeout(()=>window.print(),400));
</script>
</body></html>
"""


# ─────────────────────────────────────────────────────────────────────────────
#  Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML, specs=_list_specs())


@app.route("/api/run", methods=["POST"])
def api_run():
    data     = request.get_json(force=True)
    flow     = data.get("flow_name", "").strip().upper()
    location = (data.get("location") or "").strip() or None
    sr       = bool(data.get("self_review_enabled", True))

    if not flow:
        return {"error": "flow_name is required"}, 400

    run_id = str(uuid.uuid4())
    q: queue.Queue = queue.Queue()
    with _runs_lock:
        _runs[run_id] = q

    t = threading.Thread(target=_run_pipeline, args=(run_id, flow, location, sr), daemon=True)
    t.start()

    return {"run_id": run_id}


@app.route("/api/events/<run_id>")
def api_events(run_id: str):
    with _runs_lock:
        q = _runs.get(run_id)
    if q is None:
        abort(404)

    def generate():
        try:
            while True:
                try:
                    event = q.get(timeout=25)
                except queue.Empty:
                    yield "data: {\"type\":\"heartbeat\"}\n\n"
                    continue

                if event is None:      # sentinel — pipeline finished
                    yield "data: {\"type\":\"done\"}\n\n"
                    break

                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            with _runs_lock:
                _runs.pop(run_id, None)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/spec/<filename>")
def spec(filename: str):
    md_text = _load_md(filename)
    return render_template_string(
        SPEC_TEMPLATE,
        title=_spec_title(filename), filename=filename,
        toc=_build_toc(md_text), body=_md_to_html(md_text),
    )


@app.route("/print/<filename>")
def print_view(filename: str):
    md_text = _load_md(filename)
    return render_template_string(
        PRINT_TEMPLATE,
        title=_spec_title(filename), filename=filename,
        body=_md_to_html(md_text),
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    print(f"  Dashboard  →  http://{args.host}:{args.port}")
    print(f"  Press Ctrl+C to stop")
    print()
    app.run(host=args.host, port=args.port, debug=False, threaded=True)
