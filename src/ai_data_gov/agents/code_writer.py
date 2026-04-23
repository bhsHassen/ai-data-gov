"""
Code writer — parses the delimited output from Developer/Reviewer and
writes each file to code_output/<flow>/.

Delimiter contract:
    === FILE: <ClassName>.java ===
    <contents of ClassName.java>
    === FILE: <NextClass>.java ===
    <contents of NextClass.java>

Also emits a templated README.md explaining integration steps.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


_FILE_DELIMITER = re.compile(r"^===\s*FILE:\s*(.+?)\s*===\s*$", re.MULTILINE)


def parse_files(delimited: str) -> list[tuple[str, str]]:
    """
    Splits a delimited blob into a list of (filename, content) tuples.

    Preserves order of appearance. Trims leading/trailing whitespace on each
    content block. Ignores content before the first delimiter (if any).

    Example:
        >>> parse_files('=== FILE: A.java ===\\npublic class A {}\\n'
        ...             '=== FILE: B.java ===\\npublic class B {}\\n')
        [('A.java', 'public class A {}'), ('B.java', 'public class B {}')]
    """
    if not delimited:
        return []

    matches = list(_FILE_DELIMITER.finditer(delimited))
    if not matches:
        return []

    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        filename = m.group(1).strip()
        start    = m.end()
        end      = matches[i + 1].start() if i + 1 < len(matches) else len(delimited)
        content  = delimited[start:end].strip("\r\n").rstrip() + "\n"
        out.append((filename, content))
    return out


# --------------------------------------------------------------------------- #
#  README template                                                              #
# --------------------------------------------------------------------------- #

_README_TEMPLATE = """# Spring Batch 5 — {flow_name}

Generated: **{generated_at}**
Source spec: `{spec_filename}`

This folder contains the Spring Batch 5 / Java 17 skeleton generated from the
flow specification. It is **not a runnable project on its own** — drop the
classes into an existing Spring Boot application (or scaffold a new one with
the integration steps below).

## Files

{file_list}

## Integration steps

1. Create (or reuse) a Spring Boot 3.x project with Spring Batch 5.x on the
   classpath. Minimal Maven dependencies:

   ```xml
   <dependency>
     <groupId>org.springframework.boot</groupId>
     <artifactId>spring-boot-starter-batch</artifactId>
   </dependency>
   <dependency>
     <groupId>org.springframework.boot</groupId>
     <artifactId>spring-boot-starter-jdbc</artifactId>
   </dependency>
   <dependency>
     <groupId>com.oracle.database.jdbc</groupId>
     <artifactId>ojdbc11</artifactId>
     <scope>runtime</scope>
   </dependency>
   ```

2. Drop the generated `.java` files into `src/main/java/<your.package>/{flow_name_lower}/`
   and add the package declaration at the top of each file.

3. Add a minimal `Application.java`:

   ```java
   @SpringBootApplication
   @EnableBatchProcessing
   public class Application {{
       public static void main(String[] args) {{
           SpringApplication.run(Application.class, args);
       }}
   }}
   ```

4. Configure your datasource in `application.yml`:

   ```yaml
   spring:
     datasource:
       url: jdbc:oracle:thin:@//host:1521/SERVICE
       username: ${{DB_USER}}
       password: ${{DB_PASSWORD}}
     batch:
       jdbc:
         initialize-schema: never   # Spring Batch metadata tables already exist
       job:
         enabled: true
   ```

5. Launch the job:

   ```bash
   mvn spring-boot:run -Dspring-boot.run.arguments="--spring.batch.job.name={flow_name_lower}Job"
   ```

## Notes

- All classes target Spring Batch **5.x** API (`JobBuilder`, `StepBuilder` with
  explicit `JobRepository` and `PlatformTransactionManager`). The deprecated
  `JobBuilderFactory` / `StepBuilderFactory` from v4 is **not** used.
- Field names preserve the casing from the source spec
  (`UPPER_SNAKE_CASE` for DDL columns, `camelCase` for bean fields).
- Any JavaDoc comment containing `[INFORMATION NOT FOUND — source required]`
  means the source spec was silent on that point — do **not** invent; update
  the spec first, then regenerate.
"""


# --------------------------------------------------------------------------- #
#  Public API                                                                   #
# --------------------------------------------------------------------------- #

def write_files(
    flow_name:     str,
    final_code:    str,
    spec_filename: str = "",
    base_dir:      str = "code_output",
) -> dict:
    """
    Parses the final delimited code blob and writes each file plus a README.

    Args:
        flow_name:     Flow identifier — used to build the output subfolder.
        final_code:    Delimited string from the Reviewer.
        spec_filename: Source spec filename (for README stamping).
        base_dir:      Parent directory (default: ./code_output).

    Returns:
        {
            "output_dir":   "<base_dir>/<flow_name>",
            "output_paths": ["<abs_path_1>", ...],
            "filenames":    ["File1.java", ...],
        }
    """
    out_dir = Path(base_dir) / flow_name
    out_dir.mkdir(parents=True, exist_ok=True)

    files = parse_files(final_code)

    written:   list[str] = []
    filenames: list[str] = []

    for fname, content in files:
        # Safety — never allow traversal in filenames coming from the LLM.
        safe_name = Path(fname).name
        target    = out_dir / safe_name
        target.write_text(content, encoding="utf-8")
        written.append(str(target.resolve()))
        filenames.append(safe_name)

    # README — templated, not LLM-generated.
    readme = _README_TEMPLATE.format(
        flow_name       = flow_name,
        flow_name_lower = flow_name.lower(),
        generated_at    = datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        spec_filename   = spec_filename or "(unknown)",
        file_list       = "\n".join(f"- `{n}`" for n in filenames) or "- (no files parsed)",
    )
    readme_path = out_dir / "README.md"
    readme_path.write_text(readme, encoding="utf-8")
    written.append(str(readme_path.resolve()))
    filenames.append("README.md")

    return {
        "output_dir":   str(out_dir.resolve()),
        "output_paths": written,
        "filenames":    filenames,
    }
