# Architecture — AI Flow Documentation POC

## Overview

This POC automatically generates data flow specifications from legacy source code, DDL files and existing documentation. It uses a multi-agent pipeline orchestrated by LangGraph, where each agent has a single responsibility and communicates through a shared state object.

---

## Pipeline

```
START
  │
  ▼
┌─────────────┐
│  COLLECTOR  │  Reads source files, DDL, docs from local directories
└──────┬──────┘
       │
       ▼
┌──────────────────┐
│  MULTI-ANALYST   │  Runs Analyst 1 (Qwen3) + Analyst 2 (Codestral) in parallel
└──────┬───────────┘
       │ two independent spec drafts
       ▼
┌─────────────┐
│    JUDGE    │  Verifies both drafts against source artifacts → synthesizes best spec
└──────┬──────┘
       │
       ▼
┌─────────────────┐
│  SELF-REVIEW    │  Judge reviews its own output → improves precision and clarity
└──────┬──────────┘
       │
       ▼
┌─────────────┐
│  VALIDATOR  │  Checks all 7 sections are present
└──────┬──────┘
       │
       ├── OK ──────────────────────────────────┐
       │                                        ▼
       │                                 ┌─────────────┐
       └── KO (retry < 3) ─────────────► │   WRITER    │  Writes FLOW_<NAME>_SPEC.md
           KO (retry = 3) ──────────────► └─────────────┘
                │                               │
                ▼                              END
         back to MULTI-ANALYST
```

---

## The Orchestrator — LangGraph and FlowState

LangGraph acts as the orchestrator. It does not contain any business logic — its only role is to:
1. Pass the shared state from node to node
2. Execute conditional routing (validator → retry or write)
3. Handle the parallel execution of the two analysts

### What is FlowState ?

`FlowState` is a Python `TypedDict` — a shared dictionary that every agent reads from and writes to. LangGraph passes it through the entire pipeline, merging each node's output into the state automatically.

```python
class FlowState(TypedDict):

    # ── INPUT ──────────────────────────────────────────────────
    flow_name: str          # e.g. "TIERS_LEI"
    location: str | None    # e.g. "Sydney", "London" (optional)

    # ── COLLECTOR OUTPUT ───────────────────────────────────────
    source_files_count: int # number of source files collected
    ddl_files_count:    int # number of DDL files collected
    doc_files_count:    int # number of doc files collected
    raw_context:        str # full concatenated text sent to analysts
                            # contains: source code + DDL + existing docs

    # ── ANALYST OUTPUT ─────────────────────────────────────────
    spec_drafts: dict       # { "qwen3": "...", "codestral": "..." }
                            # one draft per model, stored independently

    # ── JUDGE / SELF-REVIEW OUTPUT ─────────────────────────────
    spec_draft: str         # final synthesized + self-reviewed spec

    # ── VALIDATOR OUTPUT ───────────────────────────────────────
    validation_ok:     bool       # True if all 7 sections present
    validation_errors: list[str]  # missing section headers

    # ── CONTROL ────────────────────────────────────────────────
    retry_count: int        # current attempt number (max 3)

    # ── WRITER OUTPUT ──────────────────────────────────────────
    output_path: str | None # path to the written .md file
```

### How LangGraph manages the state

Each node receives the full current state and returns only the fields it modifies. LangGraph merges the returned dict into the state automatically before passing it to the next node.

```
Initial state                    After Collector              After Multi-Analyst
──────────────────────────────   ────────────────────────     ─────────────────────────
flow_name:    "TIERS_LEI"        flow_name:    "TIERS_LEI"    flow_name:    "TIERS_LEI"
location:     "Sydney"           location:     "Sydney"        location:     "Sydney"
raw_context:  ""                 raw_context:  "=== DDL ..."   raw_context:  "=== DDL ..."
spec_drafts:  {}                 spec_drafts:  {}              spec_drafts:  {"qwen3": "...",
spec_draft:   ""                 spec_draft:   ""                             "codestral": "..."}
retry_count:  0                  retry_count:  0               retry_count:  1
...                              ...                           ...
```

---

## Agent Responsibilities

### Collector (`agents/collector.py`)

**Input from state:** `flow_name`

**What it does:**
- Scans `SOURCE_DIR` for `*ImportWork.java`, `*Bean.java`, and `*<FLOW_NAME>*.xml`
- Applies a content filter: keeps only files whose content mentions the flow name (in any variant: `TIERS_LEI`, `TiersLei`, `tierslei`...)
- Reads all DDL files from `DDL_DIR`
- Reads all existing docs from `DOCS_DIR`
- Concatenates everything into a single `raw_context` string with clear section separators

**Output to state:** `source_files_count`, `ddl_files_count`, `doc_files_count`, `raw_context`

**Tool exposed to Analyst:** `get_file(filename)` — allows the Analyst to request any file not returned by the initial collection

---

### Multi-Analyst (`agents/analyst.py`)

**Input from state:** `raw_context`, `flow_name`, `location`, `validation_errors` (on retry)

**What it does:**
- Runs Analyst 1 (Qwen3) and Analyst 2 (Codestral) **in parallel** using `ThreadPoolExecutor`
- Each analyst receives the same `raw_context` and generates an independent spec draft
- Each analyst can call `get_file(filename)` via tool use if it needs additional files (up to 5 tool calls)
- On retry, validation errors from the previous attempt are appended to the prompt

**Output to state:** `spec_drafts` (dict), `retry_count`

---

### Judge (`agents/judge.py`)

**Input from state:** `raw_context`, `spec_drafts`, `flow_name`, `location`

**What it does:**
- Receives the original source artifacts (ground truth) + both analyst drafts
- Verifies each draft against the source artifacts
- Synthesizes a single superior spec by taking the most accurate and complete information from both
- Corrects errors or gaps both analysts missed

**Output to state:** `spec_draft`

---

### Self-Review (`agents/judge.py — self_review()`)

**Input from state:** `raw_context`, `spec_draft`, `flow_name`, `location`

**What it does:**
- The Judge reviews its own synthesized spec against the source artifacts
- Upgrades confidence levels where additional evidence is found
- Fills gaps and clarifies vague descriptions
- Flags any invented content with `[INFORMATION NOT FOUND — source required]`

**Output to state:** `spec_draft` (improved)

---

### Validator (`agents/validator.py`)

**Input from state:** `spec_draft`

**What it does:**
- Checks that all 7 required section headers are present in the spec
- Returns `validation_ok = True` if all sections found
- Returns `validation_errors` listing missing sections if any

**Routing logic:**
- `validation_ok = True` → route to Writer
- `validation_ok = False` + `retry_count < 3` → route back to Multi-Analyst
- `validation_ok = False` + `retry_count = 3` → route to Writer (partial spec)

**Output to state:** `validation_ok`, `validation_errors`

---

### Writer (`agents/writer.py`)

**Input from state:** `flow_name`, `location`, `spec_draft`, `validation_ok`, `validation_errors`

**What it does:**
- Creates the `output/` directory if it does not exist
- Writes the spec to `FLOW_<NAME>_SPEC.md` (or `FLOW_<NAME>_<LOCATION>_SPEC.md`)
- Adds a header with generation date and status (COMPLETE / PARTIAL)
- Lists missing sections in the header if status is PARTIAL

**Output to state:** `output_path`

---

## Configuration

All paths and model names are configured in two files:

**`config.properties`** — file system paths
```properties
collector.source.path=C:/path/to/legacy/source
collector.ddl.path=C:/path/to/ddl
collector.docs.path=C:/path/to/docs
```

**`.env`** — LLM endpoint and model names
```properties
LLM_BASE_URL=https://internal-endpoint/v1
LLM_API_KEY=your-key
LLM_MODEL_ANALYST1=qwen3
LLM_MODEL_ANALYST2=codestral
LLM_MODEL_JUDGE=gpt-oss-120b
SSL_VERIFY=false
```

---

## Output

Each run produces one Markdown file in `output/`:

```
output/
  FLOW_TIERS_LEI_SPEC.md          # without location
  FLOW_TIERS_LEI_SYDNEY_SPEC.md   # with location
```

The file contains a 7-section specification:

| Section | Content | Audience |
|---------|---------|---------|
| 1. Overview | Plain-language description + summary table | Business + Technical |
| 2. Source | Field table with Type, Length, Offset, Confidence | Technical |
| 3. Transformation | Business rules with 🟢🟡🔴 confidence | Both |
| 4. Target | Field → source mapping | Technical |
| 5. Lineage | Data journey narrative + lineage table | Both |
| 6. Quality | Checks and actions | Both |
| 7. Spring Batch | Reader/Processor/Writer guidelines | Technical |
