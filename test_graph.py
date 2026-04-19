"""
Graph skeleton test — runs the full pipeline end-to-end with stubs.

Usage:
  python test_graph.py <FLOW_NAME>
  python test_graph.py TIERS_LEI

Expected output:
  Pipeline executes all 4 nodes in order:
  Collector → Analyst → Validator → Writer
"""

import sys
from src.ai_data_gov.graph import app

if len(sys.argv) < 2:
    print("Usage: python test_graph.py <FLOW_NAME>")
    print("Example: python test_graph.py TIERS_LEI")
    sys.exit(1)

flow_name = sys.argv[1]

print(f"Running pipeline for flow: {flow_name}")
print("-" * 45)

initial_state = {
    "flow_name":          flow_name,
    "source_files_count": 0,
    "ddl_files_count":    0,
    "doc_files_count":    0,
    "raw_context":        "",
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
print(f"  source files : {result['source_files_count']}")
print(f"  ddl files    : {result['ddl_files_count']}")
print(f"  doc files    : {result['doc_files_count']}")
print(f"  spec_draft   : {result['spec_draft'][:60]}...")
print(f"  validation   : {'OK' if result['validation_ok'] else 'KO'}")
print(f"  retries      : {result['retry_count']}")
print(f"  output_path  : {result['output_path']}")
print()
print("Pipeline OK — skeleton is operational")
