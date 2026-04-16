"""
Collector agent — scans legacy_code/ and returns file contents.
No LLM dependency. Testable standalone.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

SUPPORTED_EXTENSIONS = {".java", ".sql", ".xml", ".properties"}


@dataclass
class SourceFile:
    name: str
    path: str
    extension: str
    content: str


@dataclass
class CollectorOutput:
    files: list[SourceFile] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        return len(self.files)

    def summary(self) -> str:
        by_ext: dict[str, int] = {}
        for f in self.files:
            by_ext[f.extension] = by_ext.get(f.extension, 0) + 1
        parts = [f"{count} {ext}" for ext, count in sorted(by_ext.items())]
        return f"Collected {self.file_count} file(s): {', '.join(parts)}"


def collect(legacy_code_dir: str | None = None) -> CollectorOutput:
    """
    Reads all supported files from legacy_code_dir.
    Falls back to LEGACY_CODE_DIR env var, then ./legacy_code.
    """
    directory = Path(legacy_code_dir or os.getenv("LEGACY_CODE_DIR", "./legacy_code"))

    output = CollectorOutput()

    if not directory.exists():
        output.errors.append(f"Directory not found: {directory}")
        return output

    for file_path in sorted(directory.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            output.files.append(
                SourceFile(
                    name=file_path.name,
                    path=str(file_path),
                    extension=file_path.suffix.lower(),
                    content=content,
                )
            )
        except Exception as e:
            output.errors.append(f"Error reading {file_path}: {e}")

    return output
