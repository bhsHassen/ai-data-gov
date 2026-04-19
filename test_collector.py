"""
Validation script for the Collector agent.

Usage:
  python test_collector.py

Expected output:
  - Summary of files collected per category
  - List of source files (filtered by SweetDev patterns)
  - List of DDL files
  - List of doc files
  - Any errors

Setup:
  1. Edit config.properties with your local paths
  2. Place some files in the configured directories
  3. Run this script
"""

from src.ai_data_gov.agents.collector import collect

output = collect()

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
    print(f"SOURCE FILES ({len(output.source_files)}) — *ImportWork.java, *Bean.java, *.xml")
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
