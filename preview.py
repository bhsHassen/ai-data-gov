"""
Markdown preview server — renders output/ specs in a Confluence-like style.

Usage:
  python preview.py
  python preview.py --port 8080

Then open: http://localhost:5000
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

import markdown
from flask import Flask, abort, render_template_string

OUTPUT_DIR = Path("output")

app = Flask(__name__)

# --------------------------------------------------------------------------- #
#  HTML templates                                                               #
# --------------------------------------------------------------------------- #

INDEX_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Data Gov — Specs</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           background: #f4f5f7; color: #172b4d; }

    .topbar { background: #0052cc; color: #fff; padding: 14px 32px;
              display: flex; align-items: center; gap: 12px; }
    .topbar svg { width: 28px; height: 28px; }
    .topbar span { font-size: 18px; font-weight: 600; }

    .container { max-width: 800px; margin: 48px auto; padding: 0 24px; }
    h2 { font-size: 20px; font-weight: 600; color: #172b4d; margin-bottom: 20px; }

    .card-list { display: flex; flex-direction: column; gap: 12px; }
    .card { background: #fff; border-radius: 6px; border: 1px solid #dfe1e6;
            padding: 18px 24px; display: flex; align-items: center;
            justify-content: space-between; transition: box-shadow .15s; }
    .card:hover { box-shadow: 0 2px 8px rgba(0,0,0,.12); }
    .card-title { font-size: 15px; font-weight: 600; color: #172b4d; }
    .card-meta  { font-size: 12px; color: #6b778c; margin-top: 4px; }
    .card a     { text-decoration: none; color: #0052cc; font-size: 13px;
                  font-weight: 500; border: 1px solid #0052cc; border-radius: 3px;
                  padding: 5px 14px; white-space: nowrap; }
    .card a:hover { background: #0052cc; color: #fff; }

    .empty { text-align: center; color: #6b778c; margin-top: 60px; font-size: 15px; }
  </style>
</head>
<body>
  <div class="topbar">
    <svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect width="32" height="32" rx="6" fill="#0065FF"/>
      <path d="M8 22l4-8 4 5 4-9 4 12" stroke="#fff" stroke-width="2.2"
            stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
    <span>AI Data Gov — Flow Specifications</span>
  </div>

  <div class="container">
    <h2>Generated specifications</h2>

    {% if specs %}
    <div class="card-list">
      {% for spec in specs %}
      <div class="card">
        <div>
          <div class="card-title">{{ spec.title }}</div>
          <div class="card-meta">{{ spec.size }} KB &nbsp;·&nbsp; {{ spec.mtime }}</div>
        </div>
        <a href="/spec/{{ spec.filename }}">Open →</a>
      </div>
      {% endfor %}
    </div>
    {% else %}
    <div class="empty">
      No spec found in <code>output/</code>.<br>
      Run <code>python test_graph.py &lt;FLOW_NAME&gt;</code> first.
    </div>
    {% endif %}
  </div>
</body>
</html>
"""

SPEC_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ title }}</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           background: #f4f5f7; color: #172b4d; }

    /* ── Top bar ── */
    .topbar { background: #0052cc; color: #fff; padding: 14px 32px;
              display: flex; align-items: center; justify-content: space-between; }
    .topbar-left { display: flex; align-items: center; gap: 12px; }
    .topbar svg  { width: 24px; height: 24px; }
    .topbar span { font-size: 16px; font-weight: 600; }
    .topbar a    { color: #fff; font-size: 13px; text-decoration: none;
                   border: 1px solid rgba(255,255,255,.5); border-radius: 3px;
                   padding: 5px 14px; }
    .topbar a:hover { background: rgba(255,255,255,.15); }

    /* ── Layout ── */
    .layout { display: flex; min-height: calc(100vh - 52px); }

    /* ── Sidebar ── */
    .sidebar { width: 240px; flex-shrink: 0; background: #fff;
               border-right: 1px solid #dfe1e6; padding: 24px 16px;
               position: sticky; top: 0; height: calc(100vh - 52px);
               overflow-y: auto; }
    .sidebar h3 { font-size: 11px; font-weight: 700; text-transform: uppercase;
                  letter-spacing: .06em; color: #6b778c; margin-bottom: 12px; }
    .sidebar ul { list-style: none; }
    .sidebar li { margin-bottom: 4px; }
    .sidebar a  { display: block; font-size: 13px; color: #172b4d; text-decoration: none;
                  padding: 5px 10px; border-radius: 3px; }
    .sidebar a:hover { background: #f4f5f7; color: #0052cc; }

    /* ── Content ── */
    .content { flex: 1; padding: 40px 56px; max-width: 1060px; }

    h1 { font-size: 28px; font-weight: 700; color: #172b4d;
         border-bottom: 2px solid #dfe1e6; padding-bottom: 14px; margin-bottom: 28px; }
    h2 { font-size: 20px; font-weight: 700; color: #172b4d;
         margin: 36px 0 12px; padding-top: 8px; }
    h3 { font-size: 15px; font-weight: 600; color: #172b4d; margin: 20px 0 8px; }
    p  { font-size: 14px; line-height: 1.7; color: #172b4d; margin-bottom: 12px; }

    /* ── Tables ── */
    table { width: 100%; border-collapse: collapse; font-size: 13px;
            margin: 14px 0 20px; }
    thead th { background: #f4f5f7; color: #172b4d; font-weight: 600;
               text-align: left; padding: 9px 12px;
               border: 1px solid #dfe1e6; white-space: nowrap; }
    tbody td { padding: 8px 12px; border: 1px solid #dfe1e6;
               vertical-align: top; line-height: 1.5; }
    tbody tr:nth-child(even) { background: #fafbfc; }
    tbody tr:hover { background: #ebecf0; }

    /* ── Blockquote (confidence notes) ── */
    blockquote { border-left: 3px solid #ffab00; background: #fffae6;
                 padding: 8px 14px; margin: 6px 0 12px; border-radius: 0 4px 4px 0;
                 font-size: 13px; color: #172b4d; }

    /* ── Inline code ── */
    code { background: #f4f5f7; border: 1px solid #dfe1e6; border-radius: 3px;
           font-family: "SFMono-Regular", Consolas, monospace;
           font-size: 12px; padding: 1px 5px; }

    /* ── Status badge in header ── */
    .badge-complete { display: inline-block; background: #e3fcef; color: #006644;
                      font-size: 11px; font-weight: 700; padding: 3px 10px;
                      border-radius: 3px; margin-left: 10px; vertical-align: middle; }
    .badge-partial  { display: inline-block; background: #fff0b3; color: #974f0c;
                      font-size: 11px; font-weight: 700; padding: 3px 10px;
                      border-radius: 3px; margin-left: 10px; vertical-align: middle; }

    /* ── Confidence pictograms ── */
    td { word-break: break-word; }
  </style>
</head>
<body>
  <div class="topbar">
    <div class="topbar-left">
      <svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
        <rect width="32" height="32" rx="6" fill="#0065FF"/>
        <path d="M8 22l4-8 4 5 4-9 4 12" stroke="#fff" stroke-width="2.2"
              stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      <span>{{ title }}</span>
    </div>
    <a href="/">← All specs</a>
  </div>

  <div class="layout">
    <nav class="sidebar">
      <h3>Contents</h3>
      <ul>
        {% for item in toc %}
        <li><a href="#{{ item.anchor }}">{{ item.label }}</a></li>
        {% endfor %}
      </ul>
    </nav>

    <div class="content">
      {{ body | safe }}
    </div>
  </div>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
#  Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _slugify(text: str) -> str:
    """Convert heading text to a URL-friendly anchor."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _build_toc(md_text: str) -> list[dict]:
    """Extract ## headings for sidebar navigation."""
    toc = []
    for line in md_text.splitlines():
        if line.startswith("## "):
            label  = line[3:].strip()
            anchor = _slugify(label)
            toc.append({"label": label, "anchor": anchor})
    return toc


def _md_to_html(md_text: str) -> str:
    """Convert Markdown to HTML, injecting id anchors on ## headings."""
    def add_anchor(m: re.Match) -> str:
        hashes, text = m.group(1), m.group(2).strip()
        level  = len(hashes)
        anchor = _slugify(text)
        return f'<h{level} id="{anchor}">{text}</h{level}>'

    # Convert Markdown → HTML
    html = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "nl2br"],
    )
    # Inject id anchors (markdown lib doesn't do this by default)
    html = re.sub(r"<h(\d)>(.*?)</h\1>", lambda m: (
        f'<h{m.group(1)} id="{_slugify(m.group(2))}">{m.group(2)}</h{m.group(1)}>'
    ), html, flags=re.DOTALL)

    return html


def _spec_title(filename: str) -> str:
    """Turn FLOW_TIERS_LEI_SPEC.md into a readable title."""
    name = filename.replace("FLOW_", "").replace("_SPEC.md", "").replace("_", " ")
    return name.title()


def _list_specs() -> list[dict]:
    if not OUTPUT_DIR.exists():
        return []
    specs = []
    for f in sorted(OUTPUT_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        st = f.stat()
        from datetime import datetime
        specs.append({
            "filename": f.name,
            "title":    _spec_title(f.name),
            "size":     round(st.st_size / 1024, 1),
            "mtime":    datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    return specs


# --------------------------------------------------------------------------- #
#  Routes                                                                       #
# --------------------------------------------------------------------------- #

@app.route("/")
def index():
    return render_template_string(INDEX_TEMPLATE, specs=_list_specs())


@app.route("/spec/<filename>")
def spec(filename: str):
    # Security: only serve files from output/ with .md extension
    if ".." in filename or not filename.endswith(".md"):
        abort(400)

    path = OUTPUT_DIR / filename
    if not path.exists():
        abort(404)

    md_text = path.read_text(encoding="utf-8")
    toc     = _build_toc(md_text)
    body    = _md_to_html(md_text)
    title   = _spec_title(filename)

    return render_template_string(SPEC_TEMPLATE, title=title, toc=toc, body=body)


# --------------------------------------------------------------------------- #
#  Entry point                                                                  #
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Markdown preview server")
    parser.add_argument("--port", type=int, default=5000, help="Port (default: 5000)")
    parser.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    args = parser.parse_args()

    print(f"  Preview server running → http://{args.host}:{args.port}")
    print(f"  Serving specs from    → {OUTPUT_DIR.resolve()}")
    print(f"  Press Ctrl+C to stop")
    print()
    app.run(host=args.host, port=args.port, debug=False)
