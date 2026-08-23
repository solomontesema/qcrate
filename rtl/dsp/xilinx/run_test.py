#!/usr/bin/env python3
"""Generate DSP-2A vectors and run the FIR Compiler XSim test."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def run(command: list[str]) -> None:
    print("+", " ".join(str(item) for item in command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--open-waveform",
        action="store_true",
        help="keep the completed XSim waveform open in the Vivado GUI",
    )
    args = parser.parse_args()
    config = json.loads((ROOT / "config/build.json").read_text(encoding="utf-8"))
    settings = Path(config["vivado_settings"])
    if not settings.is_file():
        raise SystemExit(f"missing Vivado settings script: {settings}")

    run([
        "python3",
        "host/dsp_model/qcrate_dsp.py",
        "generate-fir-vectors",
        "host/dsp_model/configs/tone_1mhz.json",
        "build/dsp/fir_vectors",
        "--outputs",
        "256",
    ])
    vivado_command = [
        "bash",
        "-lc",
        'source "$1" && shift && exec "$@"',
        "qcrate-vivado-env",
        str(settings),
        config["vivado"],
        "-mode",
        "gui" if args.open_waveform else "batch",
        "-nojournal",
        "-nolog",
        "-source",
        str(ROOT / "rtl/dsp/xilinx/run_fir_xsim.tcl"),
    ]
    if args.open_waveform:
        vivado_command += ["-tclargs", "open-waveform"]
    run(vivado_command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
