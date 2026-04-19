"""
Validation script for the Collector agent.

Usage:
  python test_collector.py <FLOW_NAME>
  python test_collector.py TIERS_LEI

Expected output:
  - Summary of files collected per category
  - Source: *ImportWork.java, *Bean.java (all) + *<FLOW_NAME>*.xml (scoped)
  - DDL files (all)
  - Doc files (all)
  - Any errors

Setup:
  1. Edit config.properties with your local paths
  2. Run this script with the flow name as argument
"""

import sys
from src.ai_data_gov.agents.collector import collect

if len(sys.argv) < 2:
    print("Usage: python test_collector.py <FLOW_NAME>")
    print("Example: python test_collector.py TIERS_LEI")
    sys.exit(1)

flow_name = sys.argv[1]
print(f"Collecting context for flow: {flow_name}\n")
output = collect(flow_name=flow_name)

# Errors
if output.errors:
    print("ERRORS:")
    for e in output.errors:
        print(f"  ! {e}")
    print()

# Summary
print(output.summary())
print()

# Source files
if output.source_files:
    print(f"SOURCE FILES ({len(output.source_files)}) — *ImportWork.java, *Bean.java + *{flow_name}*.xml")
    for f in output.source_files:
        print(f"  [{f.extension}] {f.name}  ({len(f.content)} chars)")
else:
    print("SOURCE FILES — none found (check collector.source.path in config.properties)")

print()

# DDL files
if output.ddl_files:
    print(f"DDL FILES ({len(output.ddl_files)})")
    for f in output.ddl_files:
        print(f"  [{f.extension}] {f.name}  ({len(f.content)} chars)")
else:
    print("DDL FILES — none found (check collector.ddl.path in config.properties)")

print()

# Doc files
if output.doc_files:
    print(f"DOC FILES ({len(output.doc_files)})")
    for f in output.doc_files:
        print(f"  [{f.extension}] {f.name}  ({len(f.content)} chars)")
else:
    print("DOC FILES — none found (check collector.docs.path in config.properties)")
