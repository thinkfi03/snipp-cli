"""Benchmark harness: reduction × fidelity × wall_time on fixtures."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table

from snipp.config import Config
from snipp.core import compress_output

console = Console()


_BUILTIN_FIXTURES = [
    (
        "grep_large.txt",
        "grep -r TODO .",
        "src/api/users.py:42:# TODO: validate input\n" * 800
        + "Binary file node_modules/.cache matches\n" * 50,
    ),
    (
        "pytest_failures.txt",
        "pytest -v",
        "============================= test session starts ==============================\n"
        + "tests/test_a.py::test_one PASSED\n" * 500
        + "tests/test_b.py::test_two FAILED\n"
        + "    def test_two():\n>       assert 1 == 2\nE       AssertionError\n"
        + "============================= 500 passed, 1 failed in 2.3s ====================\n",
    ),
    (
        "git_log.txt",
        "git log",
        ("commit a1b2c3d4e5f6\nAuthor: Alice <a@example.com>\nDate:   Mon Jan 1 10:00:00 2024 +0000\n\n"
         "    fix: edge case in parser\n\n") * 100,
    ),
]


def run_bench(fixtures_dir: Optional[Path] = None, *, model: Optional[str] = None) -> None:
    cfg = Config.default()
    if model:
        cfg.model = model
    fixtures = []
    if fixtures_dir and fixtures_dir.exists():
        for f in sorted(fixtures_dir.glob("*.txt")):
            cmd_path = f.with_suffix(".cmd")
            cmd = cmd_path.read_text().strip() if cmd_path.exists() else ""
            fixtures.append((f.name, cmd, f.read_text()))
    else:
        fixtures = list(_BUILTIN_FIXTURES)

    table = Table(title=f"snipp benchmark (model={model or 'default'})")
    table.add_column("Fixture")
    table.add_column("Tool")
    table.add_column("Original")
    table.add_column("Compressed")
    table.add_column("Reduction")
    table.add_column("Fidelity")
    table.add_column("ms")

    for name, cmd, content in fixtures:
        start = time.perf_counter()
        r = compress_output(content, command=cmd, config=cfg, model=model)
        elapsed = (time.perf_counter() - start) * 1000
        fidelity_summary = ", ".join(
            f"{k}={v}" for k, v in list(r.fidelity.items())[:3]
        ) or "-"
        table.add_row(
            name,
            r.tool_type,
            str(r.original_tokens),
            str(r.compressed_tokens),
            f"{r.reduction_pct:.1f}%",
            fidelity_summary,
            f"{elapsed:.1f}",
        )
    console.print(table)
