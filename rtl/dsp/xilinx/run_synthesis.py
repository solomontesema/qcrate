#!/usr/bin/env python3
"""Run the generated DSP-2A FIR out-of-context synthesis check."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--part",
        help="override the FPGA part from config/build.json",
    )
    args = parser.parse_args()

    config = json.loads((ROOT / "config/build.json").read_text(encoding="utf-8"))
    settings = Path(config["vivado_settings"])
    if not settings.is_file():
        raise SystemExit(f"missing Vivado settings script: {settings}")

    part = args.part or config["part"]
    command = [
        "bash",
        "-lc",
        'source "$1" && shift && exec "$@"',
        "qcrate-vivado-env",
        str(settings),
        config["vivado"],
        "-mode",
        "batch",
        "-nojournal",
        "-nolog",
        "-source",
        str(ROOT / "rtl/dsp/xilinx/synth_fir_ooc.tcl"),
        "-tclargs",
        part,
    ]
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
