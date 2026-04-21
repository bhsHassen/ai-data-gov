# AI-Powered Flow Documentation POC

Automated data flow specification generator for CSR team — BNP Paribas CIB.

## What it does

Reads legacy Java/SQL code and Oracle DB schemas, then generates structured Markdown specs ready for Confluence and Collibra.

## Architecture

4-agent pipeline orchestrated by LangGraph:

```
Collector → Analyst (Qwen3) → Validator → Writer
```

Output: `FLOW_[NAME]_SPEC.md` with 7 standardized sections.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Fill in .env with your credentials
```

## Usage

```bash
python main.py --flow ATLAS2
```

## Project structure

```
legacy_code/    # Put legacy Java/SQL source files here
output/         # Generated spec files
src/            # Agent source code
```
