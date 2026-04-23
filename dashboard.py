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
                  pipeline_mode: str) -> None:
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
            "pipeline_mode":       pipeline_mode,
            "output_path":         None,
        }

        graph = build_graph()
        graph.invoke(initial_state)

    except Exception as exc:  # noqa: BLE001
        _console.emit_event({"type": "error", "message": str(exc)})

    finally:
        _console.detach_queue()
        q.put(None)   # sentinel → close SSE stream


def _run_code_pipeline(run_id: str, spec_filename: str, flow_name: str) -> None:
    """Runs the code-generation LangGraph pipeline in a background thread."""
    # Lazy import so dashboard boot stays fast and circular risks are avoided.
    from src.ai_data_gov.code_graph import code_app

    q = _runs[run_id]

    try:
        _console.attach_queue(q)

        initial_state = {
            "flow_name":     flow_name,
            "spec_filename": spec_filename,
            "spec_markdown": "",
            "spec_sections": {},
            "is_file_flow":  False,
            "guideline":     "",
            "dev_code":      "",
            "final_code":    "",
            "output_dir":    None,
            "output_paths":  [],
            "filenames":     [],
        }

        code_app.invoke(initial_state)

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


def _derive_flow_name_from_spec(filename: str) -> str:
    """
    Strips the FLOW_ prefix and _SPEC.md suffix — keeps the middle as-is.

    Examples:
        FLOW_ATLAS2_SPEC.md              → "ATLAS2"
        FLOW_ATLAS2_SYDNEY_SPEC.md       → "ATLAS2_SYDNEY"
        FLOW_PAYMENT_FEED_SPEC.md        → "PAYMENT_FEED"
        FLOW_PAYMENT_FEED_LONDON_SPEC.md → "PAYMENT_FEED_LONDON"

    Keeping the location in the folder name avoids collisions when code is
    generated for multiple locations of the same flow.
    """
    stem = filename
    if stem.startswith("FLOW_"):
        stem = stem[len("FLOW_"):]
    if stem.endswith("_SPEC.md"):
        stem = stem[: -len("_SPEC.md")]
    return stem or filename


# ─────────────────────────────────────────────────────────────────────────────
#  HTML  ·  shared CSS
# ─────────────────────────────────────────────────────────────────────────────

_CONTENT_CSS = """
    h1{font-size:22px;font-weight:bold;border-bottom:2px solid #ccc;
       padding-bottom:10px;margin-bottom:20px;color:#111}
    h2{font-size:16px;font-weight:bold;margin:28px 0 10px;
       border-left:3px solid #0052cc;padding-left:8px;color:#111}
    h3{font-size:14px;font-weight:bold;margin:16px 0 6px;color:#222}
    p{font-size:13px;line-height:1.7;color:#333;margin-bottom:10px}
    ul,ol{font-size:13px;line-height:1.7;margin:6px 0 10px 20px;color:#333}
    table{width:100%;border-collapse:collapse;font-size:12px;margin:10px 0 16px}
    thead th{background:#f0f0f0;color:#222;font-weight:bold;text-align:left;
             padding:7px 10px;border:1px solid #ccc}
    tbody td{padding:6px 10px;border:1px solid #ddd;vertical-align:top;
             line-height:1.5;color:#333}
    tbody tr:nth-child(even){background:#fafafa}
    blockquote{border-left:3px solid #e6a817;background:#fffdf0;
               padding:6px 12px;margin:4px 0 10px;font-size:12px;color:#555}
    code{background:#f5f5f5;border:1px solid #ddd;
         font-family:Consolas,monospace;font-size:12px;padding:1px 4px;color:#333}
    hr{border:none;border-top:1px solid #ddd;margin:20px 0}
    td{word-break:break-word}
"""

_PRINT_CSS = """
    @media print{
      .bar,.sidebar,.btn-pdf,.print-banner{display:none!important}
      .layout{display:block}
      body{background:#fff;color:#000;padding:0}
      .content{padding:0;max-width:100%}
      *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
      @page{size:A4;margin:18mm 15mm}
      h1{font-size:18pt;border-bottom:1pt solid #ccc}
      h2{page-break-before:always;break-before:page;font-size:13pt;margin-top:0}
      h2:first-of-type{page-break-before:avoid;break-before:avoid}
      table{page-break-inside:avoid;font-size:9pt}
      thead th{background:#eee!important}
      tbody tr:nth-child(even){background:#f9f9f9!important}
      tbody tr:hover{background:transparent!important}
    }
"""


# ─────────────────────────────────────────────────────────────────────────────
#  Dashboard page
# ─────────────────────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CERISE AI4DataGouv</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:Arial,sans-serif;background:#f5f5f5;color:#222;font-size:14px}
  a{color:#0052cc;text-decoration:none}
  a:hover{text-decoration:underline}

  .bar{background:#0052cc;color:#fff;padding:18px 28px;font-weight:bold;font-size:20px;
       letter-spacing:.3px;box-shadow:0 2px 6px rgba(0,0,0,.18)}
  .bar span.bar-sub{font-size:13px;font-weight:normal;opacity:.8;margin-left:14px}

  .page{max-width:960px;margin:24px auto;padding:0 16px}

  /* form */
  .box{background:#fff;border:1px solid #ccc;padding:18px 20px;margin-bottom:16px}
  .box h3{font-size:13px;color:#555;margin-bottom:12px;text-transform:uppercase}
  .row{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end}
  .fg{display:flex;flex-direction:column;gap:4px;flex:1;min-width:150px}
  .fg label{font-size:12px;color:#555}
  .fg input{border:1px solid #bbb;padding:7px 10px;font-size:14px;font-family:inherit;outline:none}
  .fg input:focus{border-color:#0052cc}
  .fg-check{display:flex;align-items:center;gap:8px;padding-bottom:8px}
  .fg-check label{font-size:13px;color:#444}
  button.run{background:#0052cc;color:#fff;border:none;padding:8px 22px;
             font-size:14px;cursor:pointer;font-family:inherit}
  button.run:hover{background:#003d99}
  button.run:disabled{background:#aaa;cursor:not-allowed}

  /* pipeline */
  .pipe-row{display:flex;align-items:center;gap:0;overflow-x:auto;padding:4px 0 8px}
  .pnode{text-align:center;padding:10px 6px;min-width:100px;border:1px solid #ccc;
         background:#fafafa;font-size:12px;color:#999;transition:all .25s}
  .pnode .ico{font-size:18px;margin-bottom:4px}
  .pnode .nm{font-weight:bold;text-transform:uppercase;font-size:11px}
  .pnode .dt{font-size:10px;color:#aaa;margin-top:3px;min-height:13px}
  .pnode.running{border-color:#0052cc;background:#e8f0fe;color:#0052cc}
  .pnode.running .nm{color:#0052cc}
  .pnode.done{border-color:#2e7d32;background:#f1f8f1;color:#2e7d32}
  .pnode.done .nm{color:#2e7d32}
  .pnode.skipped{opacity:.4}
  .pnode.error{border-color:#c62828;background:#fff0f0;color:#c62828}
  .parrow{flex-shrink:0;padding:0 4px;color:#bbb;font-size:16px;line-height:1}
  .parrow.active{color:#0052cc}
  .parrow.done{color:#2e7d32}
  .analyst-col{display:flex;flex-direction:column;gap:4px}
  .timer-row{font-size:12px;color:#888;margin-top:6px}
  .timer-row b{color:#0052cc}

  /* log */
  .log-box{background:#fff;border:1px solid #ccc;margin-bottom:16px}
  .log-hdr{background:#eee;padding:7px 14px;font-size:12px;color:#555;
           display:flex;justify-content:space-between;align-items:center}
  .dot{width:8px;height:8px;border-radius:50%;background:#ccc;display:inline-block}
  .dot.live{background:#2e7d32}
  .log-body{height:200px;overflow-y:auto;padding:10px 14px;
            font-family:Consolas,monospace;font-size:12px;line-height:1.6;background:#fff}
  .ll{display:flex;gap:8px}
  .la{font-weight:bold;min-width:72px;text-align:right;flex-shrink:0}
  .lm{color:#555;word-break:break-word}
  .la-collector{color:#1565c0}
  .la-analyst{color:#2e7d32}
  .la-judge{color:#e65100}
  .la-validator{color:#6a1b9a}
  .la-writer{color:#880e4f}
  .la-router{color:#888}
  .la-error{color:#c62828}
  .la-loader{color:#1565c0}
  .la-developer{color:#d84315}
  .la-reviewer{color:#00695c}
  .la-code_writer{color:#880e4f}

  /* code pipeline — tabbed preview */
  .tabs{display:flex;gap:2px;border-bottom:1px solid #ccc;margin-top:14px;
        overflow-x:auto;flex-wrap:nowrap}
  .tab{background:#f0f0f0;border:1px solid #ccc;border-bottom:none;
       padding:6px 14px;font-size:12px;font-family:inherit;color:#555;
       cursor:pointer;white-space:nowrap;transition:background .15s}
  .tab:hover{background:#e6e6e6}
  .tab.active{background:#fff;color:#0052cc;border-bottom:1px solid #fff;
              margin-bottom:-1px;font-weight:bold}
  .tab-content{background:#fff;border:1px solid #ccc;border-top:none;
               max-height:520px;overflow:auto;padding:0;position:relative}
  .tab-actions{position:sticky;top:0;z-index:2;
               display:flex;justify-content:space-between;align-items:center;
               padding:6px 10px;border-bottom:1px solid #eee;background:#f6f8fa}
  .tab-meta{font-family:Consolas,monospace;font-size:11px;color:#888}
  .copy-btn{background:#fff;border:1px solid #ccc;color:#555;
            padding:3px 12px;font-size:11px;cursor:pointer;font-family:inherit;
            border-radius:3px;transition:all .15s}
  .copy-btn:hover{background:#0052cc;color:#fff;border-color:#0052cc}
  .copy-btn.copied{background:#e6f4ea;color:#2e7d32;border-color:#8bc99a}
  .code-lines{margin:0;padding:10px 0;counter-reset:ln;
              font-family:'Cascadia Code','Fira Code','JetBrains Mono',Consolas,Menlo,monospace;
              font-size:13px;line-height:1.55;color:#24292e}
  .code-line{display:block;counter-increment:ln;padding:0 16px 0 60px;
             position:relative;white-space:pre;min-height:1.55em}
  .code-line::before{content:counter(ln);position:absolute;left:0;width:46px;
                     text-align:right;padding-right:10px;color:#b0b7be;
                     user-select:none;border-right:1px solid #eee;
                     font-variant-numeric:tabular-nums}
  .code-line:hover{background:#f6f8fa}
  .code-empty{color:#999;font-size:12px;padding:40px 24px;text-align:center}
  /* Java syntax tokens — Atom One Light palette (intranet-safe, no CDN) */
  .j-k{color:#a626a4;font-weight:600}        /* keyword  — purple */
  .j-t{color:#c18401}                        /* type     — ochre  */
  .j-s{color:#50a14f}                        /* string   — green  */
  .j-c{color:#a0a1a7;font-style:italic}      /* comment  — gray   */
  .j-a{color:#e45649}                        /* annot.   — red    */
  .j-n{color:#986801}                        /* number   — amber  */

  .result-files{list-style:none;margin-top:8px;padding-left:0}
  .result-files li{font-family:Consolas,monospace;font-size:12px;color:#444;
                   padding:2px 0}

  /* result */
  .result-box{border:1px solid #ccc;padding:14px 18px;margin-bottom:16px;
              display:none;background:#fff}
  .result-box.ok{border-color:#2e7d32;background:#f1f8f1}
  .result-box.partial{border-color:#e65100;background:#fff8f0}
  .result-box h3{font-size:14px;font-weight:bold;margin-bottom:6px}
  .result-box p{font-size:12px;color:#666;margin-bottom:10px}
  .rbtn{display:inline-block;padding:7px 16px;font-size:13px;
        border:1px solid #0052cc;color:#0052cc;margin-right:8px}
  .rbtn:hover{background:#0052cc;color:#fff;text-decoration:none}

  /* specs list */
  .srow{display:flex;justify-content:space-between;align-items:center;
        padding:9px 0;border-bottom:1px solid #eee;gap:12px}
  .srow:last-child{border-bottom:none}
  .sname{font-size:13px;font-weight:bold;color:#222}
  .smeta{font-size:11px;color:#888}
  .slinks a{font-size:12px;border:1px solid #ccc;padding:4px 10px;
            margin-left:6px;color:#444}
  .slinks a:hover{background:#f0f0f0;text-decoration:none}
  .empty{color:#aaa;font-size:13px;text-align:center;padding:16px 0}
</style>
</head>
<body>

<div class="bar">CERISE AI4DataGouv<span class="bar-sub">Flow Specification &amp; Code Generator</span></div>

<div class="page">

  <div class="box">
    <h3>Run Pipeline</h3>
    <div class="row">
      <div class="fg">
        <label>Flow Name</label>
        <input type="text" id="inp-flow" placeholder="e.g. ATLAS2" autocomplete="off">
      </div>
      <div class="fg">
        <label>Location (optional)</label>
        <input type="text" id="inp-location" placeholder="e.g. Sydney" autocomplete="off">
      </div>
      <div class="fg" style="flex:0">
        <label>Mode</label>
        <div style="display:flex;gap:16px;padding-bottom:8px">
          <label style="font-size:13px;color:#444;display:flex;align-items:center;gap:5px;cursor:pointer">
            <input type="radio" name="mode" id="mode-multi" value="multi" checked>
            Multi (+ Judge)
          </label>
          <label style="font-size:13px;color:#444;display:flex;align-items:center;gap:5px;cursor:pointer">
            <input type="radio" name="mode" id="mode-single" value="single">
            Single (Qwen3)
          </label>
        </div>
      </div>
      <div class="fg" style="flex:0">
        <label>&nbsp;</label>
        <button class="run" id="btn-run" onclick="startRun()">Run</button>
      </div>
    </div>
  </div>

  <div class="box" id="pipeline-card" style="display:none">
    <h3>Pipeline — <span id="stage-label" style="color:#0052cc;text-transform:none;font-weight:normal"></span></h3>
    <div class="pipe-row">
      <div class="pnode pending" id="pn-collector">
        <div class="ico">📂</div><div class="nm">Collector</div><div class="dt" id="pd-collector"></div>
      </div>
      <div class="parrow" id="pa-analyst">›</div>
      <div class="analyst-col">
        <div class="pnode pending" id="pn-analyst1">
          <div class="nm">Analyst 1</div><div class="dt" id="pd-analyst1"></div>
        </div>
        <div class="pnode pending" id="pn-analyst2">
          <div class="nm">Analyst 2</div><div class="dt" id="pd-analyst2"></div>
        </div>
      </div>
      <div class="parrow" id="pa-judge">›</div>
      <div class="pnode pending" id="pn-judge">
        <div class="ico">⚖</div><div class="nm">Judge</div><div class="dt" id="pd-judge"></div>
      </div>
      <div class="parrow" id="pa-validator">›</div>
      <div class="pnode pending" id="pn-validator">
        <div class="ico">✓</div><div class="nm">Validator</div><div class="dt" id="pd-validator"></div>
      </div>
      <div class="parrow" id="pa-writer">›</div>
      <div class="pnode pending" id="pn-writer">
        <div class="ico">📄</div><div class="nm">Writer</div><div class="dt" id="pd-writer"></div>
      </div>
    </div>
    <div class="timer-row">Elapsed: <b id="elapsed">0s</b></div>
  </div>

  <div class="log-box" id="log-card" style="display:none">
    <div class="log-hdr">
      <span>Output</span>
      <span class="dot" id="log-dot"></span>
    </div>
    <div class="log-body" id="log-body"></div>
  </div>

  <div class="result-box" id="result-card">
    <h3 id="result-title"></h3>
    <p id="result-detail"></p>
    <a href="#" class="rbtn" id="btn-view" target="_blank">View Spec</a>
    <a href="#" class="rbtn" id="btn-pdf"  target="_blank">Export PDF</a>
  </div>

  <div class="box" id="code-card" style="display:none">
    <h3>Code Generation — <span id="code-stage-label" style="color:#0052cc;text-transform:none;font-weight:normal"></span></h3>
    <div class="pipe-row">
      <div class="pnode pending" id="pn-loader">
        <div class="ico">📘</div><div class="nm">Spec Loader</div><div class="dt" id="pd-loader"></div>
      </div>
      <div class="parrow" id="pa-developer">›</div>
      <div class="pnode pending" id="pn-developer">
        <div class="ico">🛠</div><div class="nm">Developer</div><div class="dt" id="pd-developer"></div>
      </div>
      <div class="parrow" id="pa-reviewer">›</div>
      <div class="pnode pending" id="pn-reviewer">
        <div class="ico">🔍</div><div class="nm">Reviewer</div><div class="dt" id="pd-reviewer"></div>
      </div>
      <div class="parrow" id="pa-code_writer">›</div>
      <div class="pnode pending" id="pn-code_writer">
        <div class="ico">📄</div><div class="nm">Writer</div><div class="dt" id="pd-code_writer"></div>
      </div>
    </div>
    <div class="timer-row">Elapsed: <b id="code-elapsed">0s</b></div>

    <div class="tabs" id="code-tabs"></div>
    <div class="tab-content" id="code-tab-content">
      <div class="code-empty">Generated files will appear here as each agent produces them.</div>
    </div>
  </div>

  <div class="result-box" id="code-result-card">
    <h3 id="code-result-title"></h3>
    <p id="code-result-detail"></p>
    <ul class="result-files" id="code-result-files"></ul>
  </div>

  <div class="box">
    <h3>Generated Specifications</h3>
    <div id="specs-list"><div class="empty">Loading…</div></div>
  </div>

</div><!-- /page -->

<script>
const STAGE_ORDER = ["collector","analyst","judge","validator","writer"];
let _evtSource = null;
let _startTime = null;
let _timerInterval = null;

// ── Load specs list from API ─────────────────────────────────────────────────
function loadSpecs(){
  fetch("/api/specs")
    .then(r => r.json())
    .then(data => {
      const el = document.getElementById("specs-list");
      if(!data.specs || data.specs.length === 0){
        el.innerHTML = '<div class="empty">No specs yet.</div>';
        return;
      }
      el.innerHTML = data.specs.map(s =>
        '<div class="srow">' +
          '<div><div class="sname">' + escHtml(s.title) + '</div>' +
          '<div class="smeta">' + s.size + ' KB · ' + s.mtime + '</div></div>' +
          '<div class="slinks">' +
            '<a href="/spec/' + s.filename + '" target="_blank">Open</a>' +
            '<a href="/print/' + s.filename + '" target="_blank">PDF</a>' +
            '<a href="#" onclick="startCodeRun(\\''+s.filename+'\\');return false">Generate Code</a>' +
          '</div>' +
        '</div>'
      ).join('');
    })
    .catch(() => {});
}
document.addEventListener("DOMContentLoaded", loadSpecs);

// Show/hide Analyst 2 + Judge based on mode selection
function updatePipelinePreview(){
  const single = document.getElementById("mode-single").checked;
  const a2 = document.getElementById("pn-analyst2");
  const judge = document.getElementById("pn-judge");
  const paJudge = document.getElementById("pa-judge");
  if(a2) a2.style.display = single ? "none" : "";
  if(judge) judge.style.display = single ? "none" : "";
  if(paJudge) paJudge.style.display = single ? "none" : "";
}
document.querySelectorAll("input[name='mode']").forEach(r =>
  r.addEventListener("change", updatePipelinePreview)
);

function setNode(stage, state, detail){
  if(stage === "analyst"){
    const n1 = document.getElementById("pn-analyst1");
    const n2 = document.getElementById("pn-analyst2");
    if(!n1 || !n2) return;
    n1.className = "pnode " + state;
    n2.className = "pnode " + state;
    // Parse detail: start → "qwen3 + codestral · attempt 1/3"
    //               done  → "qwen3: 12,345 chars · codestral: 11,234 chars"
    if(detail){
      const parts = detail.split(" \u00b7 ");  // · separator
      if(state === "running" && parts[0].includes(" + ")){
        const models = parts[0].split(" + ");
        document.getElementById("pd-analyst1").textContent = models[0] || "";
        document.getElementById("pd-analyst2").textContent = models[1] || "";
      } else {
        document.getElementById("pd-analyst1").textContent = parts[0] || "";
        document.getElementById("pd-analyst2").textContent = parts[1] || "";
      }
    } else {
      document.getElementById("pd-analyst1").textContent = "";
      document.getElementById("pd-analyst2").textContent = "";
    }
    const arr = document.getElementById("pa-analyst");
    if(arr) arr.className = "parrow " + (state==="done" ? "done" : state==="running" ? "active" : "");
    return;
  }
  const node = document.getElementById("pn-"+stage);
  if(!node) return;
  node.className = "pnode " + state;
  if(detail !== undefined){
    const dt = document.getElementById("pd-"+stage);
    if(dt) dt.textContent = detail;
  }
  const arr = document.getElementById("pa-"+stage);
  if(arr) arr.className = "parrow " + (state==="done" ? "done" : state==="running" ? "active" : "");
}

function setStageLabel(text){ document.getElementById("stage-label").textContent = text; }

function addLog(agent, message){
  const body = document.getElementById("log-body");
  const line = document.createElement("div");
  line.className = "ll";
  line.innerHTML = `<span class="la la-${agent}">[${agent.toUpperCase()}]</span>`
                 + `<span class="lm">${escHtml(message)}</span>`;
  body.appendChild(line);
  body.scrollTop = body.scrollHeight;
}

function escHtml(s){
  return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function formatElapsed(ms){
  const s = Math.round(ms/1000);
  if(s < 60) return s+"s";
  const m = Math.floor(s/60), r = s%60;
  return m+"m "+(r<10?"0":"")+r+"s";
}

function startTimer(){
  _startTime = Date.now();
  _timerInterval = setInterval(()=>{
    document.getElementById("elapsed").textContent = formatElapsed(Date.now()-_startTime);
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

  const elapsedStr = formatElapsed(Date.now()-_startTime);
  const filename = outputPath.split(/[\\\\/]/).pop();
  const specUrl  = "/spec/"+filename;
  const pdfUrl   = "/print/"+filename;

  card.className = "result-box " + (ok ? "ok" : "partial");
  title.textContent  = ok ? "✅ Specification complete" : "⚠️ Partial specification";
  detail.textContent = filename + "  ·  generated in " + elapsedStr;
  btnV.href = specUrl;
  btnP.href = pdfUrl;
  card.style.display = "flex";
  loadSpecs();   // refresh specs list

  document.getElementById("btn-run").disabled = false;
}

function startRun(){
  const flow     = document.getElementById("inp-flow").value.trim().toUpperCase();
  const location = document.getElementById("inp-location").value.trim();
  const mode     = document.querySelector("input[name='mode']:checked").value;
  const single   = mode === "single";

  if(!flow){ document.getElementById("inp-flow").focus(); return; }

  // Reset UI
  document.getElementById("result-card").style.display = "none";
  document.getElementById("log-body").innerHTML = "";
  STAGE_ORDER.forEach(s => {
    setNode(s, "pending", "");
    const arr = document.getElementById("pa-"+s);
    if(arr) arr.className = "parrow";
  });
  document.getElementById("elapsed").textContent = "0s";
  setStageLabel("");

  // Show/hide nodes based on mode
  document.getElementById("pn-analyst2").style.display = single ? "none" : "";
  document.getElementById("pn-judge").style.display    = single ? "none" : "";
  document.getElementById("pa-judge").style.display    = single ? "none" : "";

  // Show cards
  document.getElementById("pipeline-card").style.display = "block";
  document.getElementById("log-card").style.display = "block";
  document.getElementById("log-dot").classList.add("live");
  document.getElementById("btn-run").disabled = true;

  // POST → get run_id
  fetch("/api/run", {
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body: JSON.stringify({flow_name:flow, location:location||null, pipeline_mode:mode})
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
      ["analyst","judge","validator","writer"].forEach(s=>{
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

// ─────────────────────────────────────────────────────────────────────────
//  CODE GENERATION PIPELINE
// ─────────────────────────────────────────────────────────────────────────
const CODE_STAGE_ORDER = ["loader","developer","reviewer","code_writer"];
let _codeEvtSource = null;
let _codeStartTime = null;
let _codeTimerInterval = null;
let _codeTabs = {};   // filename → { btn, pre }
let _codeActiveTab = null;

function setCodeNode(stage, state, detail){
  const node = document.getElementById("pn-"+stage);
  if(!node) return;
  node.className = "pnode " + state;
  if(detail !== undefined){
    const dt = document.getElementById("pd-"+stage);
    if(dt) dt.textContent = detail;
  }
  const arr = document.getElementById("pa-"+stage);
  if(arr) arr.className = "parrow " + (state==="done" ? "done" : state==="running" ? "active" : "");
}

function setCodeStageLabel(text){
  document.getElementById("code-stage-label").textContent = text;
}

function resetCodeTabs(){
  _codeTabs = {};
  _codeActiveTab = null;
  document.getElementById("code-tabs").innerHTML = "";
  document.getElementById("code-tab-content").innerHTML =
    '<div class="code-empty">Generated files will appear here as each agent produces them.</div>';
}

// --- Java syntax highlighting (vanilla, no CDN) ---------------------- //
const JAVA_KEYWORDS = new Set([
  "abstract","assert","boolean","break","byte","case","catch","char","class",
  "const","continue","default","do","double","else","enum","extends","final",
  "finally","float","for","goto","if","implements","import","instanceof","int",
  "interface","long","native","new","package","private","protected","public",
  "return","short","static","strictfp","super","switch","synchronized","this",
  "throw","throws","transient","try","void","volatile","while","true","false",
  "null","var","yield","record","sealed","permits","non-sealed"
]);

function escHtml(s){
  return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function highlightJava(src){
  // Single-pass tokenizer: comments | strings | annotations | numbers | idents
  // NOTE: backslashes are doubled because this JS lives inside a Python string.
  const re = /(\\/\\*[\\s\\S]*?\\*\\/)|(\\/\\/[^\\n]*)|("(?:\\\\.|[^"\\\\])*")|('(?:\\\\.|[^'\\\\])*')|(@[A-Za-z_][\\w.]*)|(\\b\\d[\\d_.]*[dflDFL]?\\b)|([A-Za-z_$][\\w$]*)/g;
  let out = "", last = 0, m;
  while((m = re.exec(src)) !== null){
    if(m.index > last) out += escHtml(src.slice(last, m.index));
    if(m[1])      out += '<span class="j-c">' + escHtml(m[1]) + '</span>';
    else if(m[2]) out += '<span class="j-c">' + escHtml(m[2]) + '</span>';
    else if(m[3]) out += '<span class="j-s">' + escHtml(m[3]) + '</span>';
    else if(m[4]) out += '<span class="j-s">' + escHtml(m[4]) + '</span>';
    else if(m[5]) out += '<span class="j-a">' + escHtml(m[5]) + '</span>';
    else if(m[6]) out += '<span class="j-n">' + escHtml(m[6]) + '</span>';
    else if(m[7]){
      const w = m[7];
      if(JAVA_KEYWORDS.has(w))       out += '<span class="j-k">' + w + '</span>';
      else if(/^[A-Z]/.test(w))      out += '<span class="j-t">' + w + '</span>';
      else                           out += escHtml(w);
    }
    last = re.lastIndex;
  }
  if(last < src.length) out += escHtml(src.slice(last));
  return out;
}

function renderCodeLines(src){
  // Highlight then split into lines, re-opening spans that cross newlines
  // (block comments are the main case).
  const html = highlightJava(src);
  const rawLines = html.split("\\n");
  const out = [];
  const openStack = [];
  for(const line of rawLines){
    const prefix = openStack.map(c => '<span class="'+c+'">').join("");
    // Scan this line to track open spans that carry into the next line.
    const tagRe = /<span class="(j-[kstcan])">|<\\/span>/g;
    let t;
    while((t = tagRe.exec(line)) !== null){
      if(t[1]) openStack.push(t[1]);
      else openStack.pop();
    }
    const suffix = openStack.map(()=>"</span>").join("");
    const body = prefix + line + suffix;
    out.push('<div class="code-line">' + (body.length ? body : " ") + '</div>');
  }
  return out.join("");
}

function showCodeTab(filename){
  _codeActiveTab = filename;
  Object.keys(_codeTabs).forEach(f => {
    _codeTabs[f].btn.className = "tab" + (f === filename ? " active" : "");
  });
  const entry = _codeTabs[filename];
  const host  = document.getElementById("code-tab-content");
  const lineCount = entry.content ? entry.content.split("\\n").length : 0;
  host.innerHTML =
    '<div class="tab-actions">' +
      '<div class="tab-meta">' + escHtml(filename) + ' · ' + lineCount + ' lines</div>' +
      '<button class="copy-btn" id="code-copy-btn">Copy</button>' +
    '</div>' +
    '<div class="code-lines">' + entry.rendered + '</div>';
  document.getElementById("code-copy-btn").onclick = () => {
    navigator.clipboard.writeText(entry.content).then(() => {
      const b = document.getElementById("code-copy-btn");
      if(!b) return;
      b.textContent = "Copied ✓";
      b.classList.add("copied");
      setTimeout(() => { b.textContent = "Copy"; b.classList.remove("copied"); }, 1500);
    });
  };
}

function upsertCodeTab(filename, content){
  let entry = _codeTabs[filename];
  if(!entry){
    const tabsEl = document.getElementById("code-tabs");
    const btn = document.createElement("button");
    btn.className = "tab";
    btn.textContent = filename;
    btn.onclick = () => showCodeTab(filename);
    tabsEl.appendChild(btn);
    entry = { btn, content: "", rendered: "" };
    _codeTabs[filename] = entry;
  }
  entry.content  = content || "";
  entry.rendered = renderCodeLines(entry.content);
  if(_codeActiveTab === null || _codeActiveTab === filename){
    showCodeTab(filename);
  }
}

function startCodeTimer(){
  _codeStartTime = Date.now();
  _codeTimerInterval = setInterval(()=>{
    document.getElementById("code-elapsed").textContent =
      formatElapsed(Date.now()-_codeStartTime);
  }, 500);
}

function stopCodeTimer(){ clearInterval(_codeTimerInterval); }

function showCodeResult(outputDir, files){
  stopCodeTimer();
  document.getElementById("log-dot").classList.remove("live");

  const card   = document.getElementById("code-result-card");
  const title  = document.getElementById("code-result-title");
  const detail = document.getElementById("code-result-detail");
  const list   = document.getElementById("code-result-files");

  const elapsedStr = formatElapsed(Date.now()-_codeStartTime);
  card.className = "result-box ok";
  title.textContent  = "✅ Code generated";
  detail.textContent = "Files saved to: " + outputDir + "  ·  generated in " + elapsedStr;
  list.innerHTML = (files||[]).map(f => "<li>• " + escHtml(f) + "</li>").join("");
  card.style.display = "block";
}

function startCodeRun(specFilename){
  // Reset UI
  document.getElementById("code-result-card").style.display = "none";
  document.getElementById("log-body").innerHTML = "";
  CODE_STAGE_ORDER.forEach(s => {
    setCodeNode(s, "pending", "");
    const arr = document.getElementById("pa-"+s);
    if(arr) arr.className = "parrow";
  });
  document.getElementById("code-elapsed").textContent = "0s";
  setCodeStageLabel(specFilename);
  resetCodeTabs();

  document.getElementById("code-card").style.display = "block";
  document.getElementById("log-card").style.display = "block";
  document.getElementById("log-dot").classList.add("live");

  fetch("/api/generate-code", {
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body: JSON.stringify({spec_filename:specFilename})
  })
  .then(r=>r.json().then(d=>({ok:r.ok, data:d})))
  .then(({ok, data})=>{
    if(!ok){
      addLog("error", data.error || "Failed to start code run");
      return;
    }
    startCodeTimer();
    connectCodeSSE(data.run_id);
  })
  .catch(err=>{ addLog("error","Failed to start code run: "+err); });
}

function connectCodeSSE(runId){
  if(_codeEvtSource) _codeEvtSource.close();
  _codeEvtSource = new EventSource("/api/code-events/"+runId);

  _codeEvtSource.onmessage = function(e){
    const ev = JSON.parse(e.data);

    if(ev.type === "heartbeat") return;

    if(ev.type === "log"){ addLog(ev.agent, ev.message); return; }

    if(ev.type === "stage_start"){
      setCodeNode(ev.stage, "running", ev.detail||"");
      setCodeStageLabel(ev.stage.replace("_"," ").toUpperCase());
      return;
    }

    if(ev.type === "stage_done"){
      setCodeNode(ev.stage, "done", ev.detail||"");
      return;
    }

    if(ev.type === "code_file"){
      upsertCodeTab(ev.filename, ev.content || "");
      return;
    }

    if(ev.type === "pipeline_complete"){
      showCodeResult(ev.output_dir, ev.files || []);
      _codeEvtSource.close();
      return;
    }

    if(ev.type === "error"){
      addLog("error", ev.message);
      stopCodeTimer();
      document.getElementById("log-dot").classList.remove("live");
      _codeEvtSource.close();
      return;
    }

    if(ev.type === "done"){ _codeEvtSource.close(); return; }
  };

  _codeEvtSource.onerror = function(){
    addLog("error", "Connection lost.");
    stopCodeTimer();
    _codeEvtSource.close();
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
  body{font-family:Arial,sans-serif;background:#f5f5f5;color:#222;font-size:14px}
  .bar{background:#0052cc;color:#fff;padding:9px 16px;display:flex;
       align-items:center;justify-content:space-between;font-size:13px}
  .bar strong{font-size:14px;font-weight:bold}
  .bar a,.bar button{color:#fff;font-size:12px;text-decoration:none;
    border:1px solid rgba(255,255,255,.5);padding:4px 12px;background:transparent;
    cursor:pointer;font-family:inherit;margin-left:8px}
  .bar a:hover,.bar button:hover{background:rgba(255,255,255,.15)}
  .layout{display:flex;min-height:calc(100vh - 38px)}
  .sidebar{width:200px;flex-shrink:0;background:#fff;border-right:1px solid #ddd;
           padding:16px 12px;position:sticky;top:0;height:calc(100vh - 38px);
           overflow-y:auto}
  .sidebar p{font-size:11px;color:#888;text-transform:uppercase;
             margin-bottom:8px;font-weight:bold}
  .sidebar ul{list-style:none}
  .sidebar li{margin-bottom:2px}
  .sidebar a{display:block;font-size:12px;color:#444;text-decoration:none;
             padding:3px 6px}
  .sidebar a:hover{color:#0052cc;text-decoration:underline}
  .content{flex:1;padding:28px 40px 60px;max-width:900px;background:#fff;
           border-left:1px solid #eee}
  """ + _CONTENT_CSS + _PRINT_CSS + """
</style>
</head>
<body>
<div class="bar">
  <strong>{{ title }}</strong>
  <div>
    <button onclick="window.open('/print/{{ filename }}','_blank')">Export PDF</button>
    <a href="/">&#8592; Dashboard</a>
  </div>
</div>
<div class="layout">
  <nav class="sidebar">
    <p>Contents</p>
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
  body{font-family:Arial,sans-serif;background:#fff;color:#222;padding:24px 40px}
  .print-bar{background:#0052cc;color:#fff;padding:9px 16px;
             margin:-24px -40px 24px;display:flex;align-items:center;
             justify-content:space-between;font-size:13px}
  .print-bar button{background:#fff;color:#0052cc;border:none;padding:5px 14px;
                    font-size:13px;font-weight:bold;cursor:pointer;font-family:inherit}
  .print-bar a{color:rgba(255,255,255,.8);font-size:12px;text-decoration:none;margin-left:12px}
  .print-bar a:hover{color:#fff}
  """ + _CONTENT_CSS + """
  @media print{
    .print-bar{display:none!important}
    body{padding:0}
    *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
    @page{size:A4;margin:18mm 15mm}
    h2{page-break-before:always;break-before:page;margin-top:0}
    h2:first-of-type{page-break-before:avoid;break-before:avoid}
    table{page-break-inside:avoid;font-size:9pt}
    thead th{background:#eee!important}
    tbody tr:nth-child(even){background:#f9f9f9!important}
    tbody tr:hover{background:transparent!important}
  }
</style>
</head>
<body>
<div class="print-bar">
  <span>File &rsaquo; Print &mdash; Destination: <strong>Save as PDF</strong> &mdash; A4</span>
  <div>
    <button onclick="window.print()">Print / Save as PDF</button>
    <a href="/spec/{{ filename }}">&#8592; Back</a>
  </div>
</div>
{{ body | safe }}
<script>
  window.addEventListener('load',function(){ setTimeout(function(){ window.print(); },400); });
</script>
</body></html>
"""


# ─────────────────────────────────────────────────────────────────────────────
#  Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    # Return raw HTML — bypass Jinja2 entirely (JS loads specs via /api/specs)
    from flask import Response as _R
    return _R(DASHBOARD_HTML, mimetype="text/html")


@app.route("/api/specs")
def api_specs():
    return {"specs": _list_specs()}


@app.route("/api/run", methods=["POST"])
def api_run():
    data     = request.get_json(force=True)
    flow     = data.get("flow_name", "").strip().upper()
    location = (data.get("location") or "").strip() or None
    mode     = data.get("pipeline_mode", "multi")
    if mode not in ("single", "multi"):
        mode = "multi"

    if not flow:
        return {"error": "flow_name is required"}, 400

    run_id = str(uuid.uuid4())
    q: queue.Queue = queue.Queue()
    with _runs_lock:
        _runs[run_id] = q

    t = threading.Thread(target=_run_pipeline, args=(run_id, flow, location, mode), daemon=True)
    t.start()

    return {"run_id": run_id}


@app.route("/api/generate-code", methods=["POST"])
def api_generate_code():
    """Kicks off the code-generation pipeline for an existing spec file."""
    data          = request.get_json(force=True) or {}
    spec_filename = (data.get("spec_filename") or "").strip()

    # Same guard as _load_md — reject traversal, require .md suffix.
    if not spec_filename or ".." in spec_filename or not spec_filename.endswith(".md"):
        return {"error": "invalid spec_filename"}, 400

    spec_path = OUTPUT_DIR / spec_filename
    if not spec_path.exists():
        return {"error": f"spec not found: {spec_filename}"}, 404

    flow_name = _derive_flow_name_from_spec(spec_filename)
    if not flow_name:
        return {"error": "could not derive flow name from filename"}, 400

    run_id = str(uuid.uuid4())
    q: queue.Queue = queue.Queue()
    with _runs_lock:
        _runs[run_id] = q

    t = threading.Thread(
        target=_run_code_pipeline,
        args=(run_id, spec_filename, flow_name),
        daemon=True,
    )
    t.start()

    return {"run_id": run_id, "flow_name": flow_name}


@app.route("/api/code-events/<run_id>")
def api_code_events(run_id: str):
    return _sse_response(run_id)


def _sse_response(run_id: str) -> Response:
    """
    Shared SSE generator — streams events from the run's queue until a
    sentinel (None) is received. Used by both the spec pipeline and the
    code-generation pipeline. Pops the queue from _runs on disconnect.
    """
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


@app.route("/api/events/<run_id>")
def api_events(run_id: str):
    return _sse_response(run_id)


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
