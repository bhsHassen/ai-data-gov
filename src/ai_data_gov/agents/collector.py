"""
Collector agent — reads legacy source files, DDL and existing docs.

Filters source files by SweetDev naming conventions + flow name:
  *ImportWork.java       → all batch job classes
  *Bean.java             → all data model classes
  *<FLOW_NAME>*.xml      → only XML files matching the flow name

DDL and docs directories are read entirely (no filter).
Paths are configured in config.properties.
"""

import fnmatch
from pathlib import Path
from dataclasses import dataclass, field
from configparser import ConfigParser


# --------------------------------------------------------------------------- #
#  Config loader                                                                #
# --------------------------------------------------------------------------- #

def _load_config(properties_path: str = "config.properties") -> ConfigParser:
    config = ConfigParser()
    # ConfigParser needs a [section] header — we fake one
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
#  Internal helpers                                                             #
# --------------------------------------------------------------------------- #

def _matches_any(filename: str, patterns: list[str]) -> bool:
    """Return True if filename matches at least one glob pattern."""
    return any(fnmatch.fnmatch(filename, p.strip()) for p in patterns)


def _read_directory(
    directory: Path,
    category: str,
    patterns: list[str] | None = None,
) -> tuple[list[SourceFile], list[str]]:
    """
    Read all files in directory (recursively).
    If patterns is provided, only files matching at least one pattern are kept.
    Returns (files, errors).
    """
    files: list[SourceFile] = []
    errors: list[str] = []

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


# --------------------------------------------------------------------------- #
#  Public API                                                                   #
# --------------------------------------------------------------------------- #

def collect(flow_name: str, properties_path: str = "config.properties") -> CollectorOutput:
    """
    Main entry point.

    Args:
        flow_name:        Name of the flow to process (e.g. "TIERS_LEI").
                          Used to filter XML files: only *<flow_name>*.xml are kept.
        properties_path:  Path to config.properties file.

    Filtering rules:
        source/ → *ImportWork.java (all), *Bean.java (all), *<flow_name>*.xml
        ddl/    → all files
        docs/   → all files
    """
    config  = _load_config(properties_path)
    section = "main"

    source_dir = Path(config.get(section, "collector.source.path"))
    ddl_dir    = Path(config.get(section, "collector.ddl.path"))
    docs_dir   = Path(config.get(section, "collector.docs.path"))

    # Build source patterns: Java patterns are fixed, XML is scoped to flow name
    java_patterns = ["*ImportWork.java", "*Bean.java"]
    xml_pattern   = f"*{flow_name}*.xml"
    source_patterns = java_patterns + [xml_pattern]

    output = CollectorOutput()

    # Source files — Java (all) + XML (flow-scoped)
    source_files, errs = _read_directory(source_dir, "source", source_patterns)
    output.source_files.extend(source_files)
    output.errors.extend(errs)

    # DDL files — all files
    ddl_files, errs = _read_directory(ddl_dir, "ddl")
    output.ddl_files.extend(ddl_files)
    output.errors.extend(errs)

    # Doc files — all files
    doc_files, errs = _read_directory(docs_dir, "doc")
    output.doc_files.extend(doc_files)
    output.errors.extend(errs)

    return output
