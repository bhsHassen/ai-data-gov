"""
Quick validation script for the Collector agent.
Run: python test_collector.py

Expected: lists all files found in legacy_code/
"""

from src.ai_data_gov.agents.collector import collect

output = collect()

if output.errors:
    print("ERRORS:")
    for e in output.errors:
        print(f"  - {e}")

if output.file_count == 0:
    print("No files found in legacy_code/ — add some .java or .sql files to test.")
else:
    print(output.summary())
    print()
    for f in output.files:
        print(f"  [{f.extension}] {f.name}  ({len(f.content)} chars)")
        print(f"         {f.path}")
