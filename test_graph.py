"""
End-to-end pipeline test.

Usage:
  python test_graph.py <FLOW_NAME> [LOCATION] [--single|--multi]
  python test_graph.py TIERS_LEI
  python test_graph.py TIERS_LEI Sydney
  python test_graph.py TIERS_LEI Sydney --single
"""

import sys
from src.ai_data_gov.graph import app

args = sys.argv[1:]

if not args:
    print("Usage: python test_graph.py <FLOW_NAME> [LOCATION] [--single|--multi]")
    print("  --single  Qwen3 only, no judge")
    print("  --multi   Qwen3 + Codestral + Judge (default)")
    sys.exit(1)

pipeline_mode = "single" if "--single" in args else "multi"
args = [a for a in args if a not in ("--single", "--multi")]

flow_name = args[0]
location  = args[1] if len(args) > 1 else None

label = f"{flow_name}" + (f" [{location}]" if location else "")
print(f"Running pipeline for : {label}")
print(f"Mode                 : {pipeline_mode}")
print("-" * 45)

initial_state = {
    "flow_name":           flow_name,
    "location":            location,
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

result = app.invoke(initial_state)

print("-" * 45)
print()
print("Final state:")
print(f"  flow_name   : {result['flow_name']}")
print(f"  location    : {result.get('location') or 'N/A'}")
print(f"  mode        : {result.get('pipeline_mode')}")
print(f"  source files: {result['source_files_count']}")
print(f"  ddl files   : {result['ddl_files_count']}")
print(f"  doc files   : {result['doc_files_count']}")
print(f"  validation  : {'OK' if result['validation_ok'] else 'KO'}")
print(f"  retries     : {result['retry_count']}")
print(f"  output_path : {result['output_path']}")
print()
print("Pipeline complete")
