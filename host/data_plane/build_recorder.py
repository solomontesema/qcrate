#!/usr/bin/env python3
"""Build the dependency-free Q-Crate host recorder with strict warnings."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "build" / "host" / "qcrate-recorder",
        help="output executable (default: build/host/qcrate-recorder)",
    )
    args = parser.parse_args()
    compiler_name = os.environ.get("CC", "cc")
    compiler = shutil.which(compiler_name)
    if compiler is None:
        raise SystemExit(f"error: C compiler not found: {compiler_name}")
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        compiler,
        "-std=c11",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Werror",
        f"-I{ROOT / 'common' / 'data_plane'}",
        f"-I{ROOT / 'host' / 'data_plane'}",
        str(ROOT / "host" / "data_plane" / "qcrate_recorder.c"),
        str(ROOT / "host" / "data_plane" / "qcrate_run_format.c"),
        str(ROOT / "common" / "data_plane" / "qcrate_data_protocol.c"),
        "-o",
        str(output),
    ]
    print("+", " ".join(command))
    subprocess.run(command, check=True, cwd=ROOT)
    print(f"PASS built {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
