"""
Collector agent — reads legacy source files, DDL and existing docs.

Source file filtering strategy:
  Step 1 — Pattern filter (SweetDev conventions):
    *ImportWork.java, *Bean.java, *<FLOW_NAME>*.xml

  Step 2 — Content filter (flow name variants):
    Keeps only files whose name or content contains the flow name
    in any of its common forms (ATLAS2, atlas2, Atlas2...)

  Result: a small, relevant set of files for the given flow.

DDL and docs: all files (no filter).

Also exposes get_file() as a tool for the Analyst to request
additional files not returned by the initial collection.
"""
from __future__ import annotations

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from dataclasses import dataclass, field
from configparser import ConfigParser


# --------------------------------------------------------------------------- #
#  Config loader                                                                #
# --------------------------------------------------------------------------- #

def _load_config(properties_path: str = "config.properties") -> ConfigParser:
    config = ConfigParser()
    with open(properties_path, encoding="utf-8") as f:
        content = "[main]\n" + f.read()
    config.read_string(content)
    return config


# --------------------------------------------------------------------------- #
#  Data structures                                                              #
# --------------------------------------------------------------------------- #

@dataclass
class SourceFile:
    name: str
    path: str
    extension: str
    category: str   # "source" | "ddl" | "doc"
    content: str


@dataclass
class CollectorOutput:
    source_files: list[SourceFile] = field(default_factory=list)
    ddl_files:    list[SourceFile] = field(default_factory=list)
    doc_files:    list[SourceFile] = field(default_factory=list)
    errors:       list[str]        = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.source_files) + len(self.ddl_files) + len(self.doc_files)

    def summary(self) -> str:
        return (
            f"Collected {self.total} file(s) — "
            f"source: {len(self.source_files)}, "
            f"ddl: {len(self.ddl_files)}, "
            f"docs: {len(self.doc_files)}"
        )


# --------------------------------------------------------------------------- #
#  Flow name variants                                                           #
# --------------------------------------------------------------------------- #

def _flow_name_variants(flow_name: str) -> list[str]:
    """
    Generates common textual variants of a flow name for content search.

    Example — flow_name = "ATLAS2":
      ATLAS2, atlas2, Atlas2
    (For underscored names like "FOO_BAR": FOO_BAR, foo_bar, FooBar, foobar, FOOBAR)
    """
    parts = flow_name.split("_")

    variants = set()
    variants.add(flow_name)                                      # ATLAS2
    variants.add(flow_name.lower())                              # atlas2
    variants.add(flow_name.replace("_", ""))                     # ATLAS2 (no underscore)
    variants.add(flow_name.lower().replace("_", ""))             # atlas2
    variants.add("".join(p.capitalize() for p in parts))         # Atlas2

    return list(variants)


def _contains_flow_name(text: str, variants: list[str]) -> bool:
    """Returns True if text contains any flow name variant (case-insensitive)."""
    text_lower = text.lower()
    return any(v.lower() in text_lower for v in variants)



# --------------------------------------------------------------------------- #
#  Internal helpers                                                             #
# --------------------------------------------------------------------------- #

def _matches_any(filename: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(filename, p.strip()) for p in patterns)


def _scan_directory(
    directory: Path,
    category: str,
    patterns: list[str] | None = None,
) -> tuple[list[SourceFile], list[str]]:
    """
    Reads all files matching patterns (or all files if no patterns).
    Does NOT apply flow name filter — used for DDL and docs.
    """
    files:  list[SourceFile] = []
    errors: list[str]        = []

    if not directory.exists():
        errors.append(f"Directory not found: {directory}")
        return files, errors

    for file_path in sorted(directory.rglob("*")):
        if not file_path.is_file():
            continue
        if patterns and not _matches_any(file_path.name, patterns):
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            files.append(SourceFile(
                name=file_path.name,
                path=str(file_path),
                extension=file_path.suffix.lower(),
                category=category,
                content=content,
            ))
        except Exception as e:
            errors.append(f"Error reading {file_path}: {e}")

    return files, errors


def _scan_source_for_flow(
    directory: Path,
    flow_name: str,
    patterns: list[str],
) -> tuple[list[SourceFile], list[str]]:
    """
    Scans source directory with two-step filtering:
      1. SweetDev pattern filter (filename)
      2. Flow name content filter (name or content contains flow name variant)
    """
    files:    list[SourceFile] = []
    errors:   list[str]        = []
    variants: list[str]        = _flow_name_variants(flow_name)

    if not directory.exists():
        errors.append(f"Directory not found: {directory}")
        return files, errors

    for file_path in sorted(directory.rglob("*")):
        if not file_path.is_file():
            continue

        # Step 1 — SweetDev pattern filter
        if not _matches_any(file_path.name, patterns):
            continue

        try:
            content    = file_path.read_text(encoding="utf-8", errors="replace")
            searchable = file_path.name + "\n" + content

            # Step 2 — Flow name filter
            if not _contains_flow_name(searchable, variants):
                continue

            files.append(SourceFile(
                name=file_path.name,
                path=str(file_path),
                extension=file_path.suffix.lower(),
                category="source",
                content=content,
            ))
        except Exception as e:
            errors.append(f"Error reading {file_path}: {e}")

    return files, errors


# --------------------------------------------------------------------------- #
#  Tool: get_file (for Analyst tool use)                                       #
# --------------------------------------------------------------------------- #

def get_file(filename: str, properties_path: str = "config.properties") -> str:
    """
    Tool exposed to the Analyst.
    Searches for a file by name in the source directory and returns its content.
    Used when the Analyst needs a file not returned by the initial collection.
    """
    config     = _load_config(properties_path)
    source_dir = Path(config.get("main", "collector.source.path"))

    for file_path in source_dir.rglob(filename):
        if file_path.is_file():
            return file_path.read_text(encoding="utf-8", errors="replace")

    return f"[FILE NOT FOUND: {filename}]"


# --------------------------------------------------------------------------- #
#  Public API                                                                   #
# --------------------------------------------------------------------------- #

def collect(
    flow_name: str,
    properties_path: str = "config.properties",
) -> CollectorOutput:
    """
    Main entry point.

    Args:
        flow_name:        Flow to process (e.g. "ATLAS2").
        properties_path:  Path to config.properties.

    Note: location is NOT used for filtering — the same source files
    manage all locations. Location context is handled by the Analyst.

    Source filtering:
        - SweetDev patterns: *ImportWork.java, *Bean.java, *<flow_name>*.xml
        - Content filter: file must reference the flow name (any variant)

    DDL + docs: all files, no filter.
    """
    config  = _load_config(properties_path)
    section = "main"

    source_dir = Path(config.get(section, "collector.source.path"))
    ddl_dir    = Path(config.get(section, "collector.ddl.path"))
    docs_dir   = Path(config.get(section, "collector.docs.path"))

    java_patterns   = ["*ImportWork.java", "*Bean.java"]
    xml_pattern     = f"*{flow_name}*.xml"
    source_patterns = java_patterns + [xml_pattern]

    output = CollectorOutput()

    # Source — pattern + flow name content filter (no location filter)
    source_files, errs = _scan_source_for_flow(source_dir, flow_name, source_patterns)
    output.source_files.extend(source_files)
    output.errors.extend(errs)

    # DDL — all files
    ddl_files, errs = _scan_directory(ddl_dir, "ddl")
    output.ddl_files.extend(ddl_files)
    output.errors.extend(errs)

    # Docs — all files
    doc_files, errs = _scan_directory(docs_dir, "doc")
    output.doc_files.extend(doc_files)
    output.errors.extend(errs)

    return output
