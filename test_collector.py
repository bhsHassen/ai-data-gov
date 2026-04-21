"""
Validation script for the Collector agent.

Usage:
  python test_collector.py <FLOW_NAME> [LOCATION]
  python test_collector.py ATLAS2
  python test_collector.py ATLAS2 Sydney

Setup:
  1. Edit config.properties with your local paths
  2. Run this script with the flow name (and optional location)
"""

import sys
from src.ai_data_gov.agents.collector import collect

if len(sys.argv) < 2:
    print("Usage: python test_collector.py <FLOW_NAME> [LOCATION]")
    print("Example: python test_collector.py ATLAS2")
    print("Example: python test_collector.py ATLAS2 Sydney")
    sys.exit(1)

flow_name = sys.argv[1]
location  = sys.argv[2] if len(sys.argv) > 2 else None

label = f"{flow_name}" + (f" [{location}]" if location else "")
print(f"Collecting context for: {label}\n")

output = collect(flow_name=flow_name)

if output.errors:
    print("ERRORS:")
    for e in output.errors:
        print(f"  ! {e}")
    print()

print(output.summary())
print()

if output.source_files:
    print(f"SOURCE FILES ({len(output.source_files)})")
    for f in output.source_files:
        print(f"  [{f.extension}] {f.name}  ({len(f.content)} chars)")
else:
    print("SOURCE FILES — none found")

print()

if output.ddl_files:
    print(f"DDL FILES ({len(output.ddl_files)})")
    for f in output.ddl_files:
        print(f"  [{f.extension}] {f.name}  ({len(f.content)} chars)")
else:
    print("DDL FILES — none found")

print()

if output.doc_files:
    print(f"DOC FILES ({len(output.doc_files)})")
    for f in output.doc_files:
        print(f"  [{f.extension}] {f.name}  ({len(f.content)} chars)")
else:
    print("DOC FILES — none found")
