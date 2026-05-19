# cobol_reverse — Mainframe COBOL/VSAM reverse-engineering pipeline

POC tool that ingests legacy COBOL modules and VSAM copybooks and produces a
complete, audit-grade specification of the application. Built to bootstrap a
migration project — but the specification stands on its own.

## Status

**MVP — Phase 1 (inspector)** : drop your source files, the tool classifies
each one (COBOL module / copybook / JCL / unknown) and surfaces fingerprints
(PROGRAM-ID, COPY statements, CALL targets, EXEC SQL/CICS markers, encoding).

## Quick start

```bash
# 1. Set up the environment
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

# 2. Drop your COBOL files (any extension) into:
input/raw/

# 3. Run the inspector
python run.py inspect
```

The inspector prints a summary to the console and writes a machine-readable
report to `output/inspect.json`.

## Project layout

```
input/raw/                       <- source files dropped here
src/cobol_reverse/
    inspect.py                   <- file classifier (no LLM)
    parsers/                     <- (next phase) COBOL + copybook parsers
    llm.py                       <- OpenAI-compatible client
    console.py                   <- coloured logger
output/                          <- generated artefacts (json, markdown)
run.py                           <- CLI entry point
```

## Next phases

1. ~~Inspector~~ — done
2. Copybook parser — hierarchical PIC tree extraction
3. COBOL parser — divisions, SELECT/ASSIGN, CALL graph, PERFORM graph
4. IR builder + static analytics
5. LLM agents (cartographer, data dictionary, program doc, business rules)
6. Validator + dashboard
