"""
End-to-end pipeline test.

Usage:
  python test_graph.py <FLOW_NAME> [LOCATION]
  python test_graph.py TIERS_LEI
  python test_graph.py TIERS_LEI Sydney
"""

import sys
from src.ai_data_gov.graph import app

if len(sys.argv) < 2:
    print("Usage: python test_graph.py <FLOW_NAME> [LOCATION]")
    print("Example: python test_graph.py TIERS_LEI")
    print("Example: python test_graph.py TIERS_LEI Sydney")
    sys.exit(1)

flow_name = sys.argv[1]
location  = sys.argv[2] if len(sys.argv) > 2 else None

label = f"{flow_name}" + (f" [{location}]" if location else "")
print(f"Running pipeline for: {label}")
print("-" * 45)

initial_state = {
    "flow_name":          flow_name,
    "location":           location,
    "source_files_count": 0,
    "ddl_files_count":    0,
    "doc_files_count":    0,
    "raw_context":        "",
    "spec_drafts":        {},
    "spec_draft":         "",
    "validation_ok":      False,
    "validation_errors":  [],
    "retry_count":        0,
    "output_path":        None,
}

result = app.invoke(initial_state)

print("-" * 45)
print()
print("Final state:")
print(f"  flow_name    : {result['flow_name']}")
print(f"  location     : {result.get('location') or 'N/A'}")
print(f"  source files : {result['source_files_count']}")
print(f"  ddl files    : {result['ddl_files_count']}")
print(f"  doc files    : {result['doc_files_count']}")
print(f"  validation   : {'OK' if result['validation_ok'] else 'KO'}")
print(f"  retries      : {result['retry_count']}")
print(f"  output_path  : {result['output_path']}")
print()
print("Pipeline complete")
