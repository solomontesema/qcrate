#!/usr/bin/env python3
"""Run the lightweight cross-language Q-Crate data-protocol tests."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMMON = ROOT / "common" / "data_plane"


def main() -> int:
    compiler = shutil.which("cc")
    if compiler is None:
        raise SystemExit("error: a C11 compiler named cc is required")
    with tempfile.TemporaryDirectory() as temporary:
        executable = Path(temporary) / "test_qcrate_data_protocol"
        subprocess.run(
            [
                compiler,
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Werror",
                f"-I{COMMON}",
                str(COMMON / "qcrate_data_protocol.c"),
                str(COMMON / "tests" / "test_qcrate_data_protocol.c"),
                "-o",
                str(executable),
            ],
            check=True,
            cwd=ROOT,
        )
        subprocess.run([str(executable)], check=True, cwd=ROOT)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "host/data_plane/tests",
            "-v",
        ],
        check=True,
        cwd=ROOT,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
