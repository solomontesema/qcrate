#!/usr/bin/env python3
"""Generate exact vectors and run DSP-2A or DSP-2B XSim tests."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
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
    parser.add_argument(
        "--test",
        choices=("fir", "chain", "all"),
        default="all",
        help="run the FIR unit test, complete DSP chain, or both",
    )
    args = parser.parse_args()
    if args.open_waveform and args.test == "all":
        parser.error("--open-waveform requires --test fir or --test chain")
    config = json.loads((ROOT / "config/build.json").read_text(encoding="utf-8"))
    settings = Path(config["vivado_settings"])
    if not settings.is_file():
        raise SystemExit(f"missing Vivado settings script: {settings}")

    run([
        sys.executable,
        "host/dsp_model/qcrate_dsp.py",
        "generate-fir-vectors",
        "host/dsp_model/configs/tone_1mhz.json",
        "build/dsp/fir_vectors",
        "--outputs",
        "256",
    ])
    tests = {
        "fir": {
            "script": ROOT / "rtl/dsp/xilinx/run_fir_xsim.tcl",
            "kernel": ROOT / "build/vivado/dsp_fir_xsim/qcrate_dsp_fir_xsim.sim/sim_1/behav/xsim/xsim.dir/qcrate_fir_decim16_tb_behav/xsimk",
            "sentinel": ROOT / "build/dsp/fir_vectors/xsim.pass",
            "started": ROOT / "build/dsp/fir_vectors/xsim.started",
        },
        "chain": {
            "script": ROOT / "rtl/dsp/xilinx/run_dsp2b_xsim.tcl",
            "kernel": ROOT / "build/vivado/dsp2b_xsim/qcrate_dsp2b_xsim.sim/sim_1/behav/xsim/xsim.dir/qcrate_dsp_stream_tb_behav/xsimk",
            "sentinel": ROOT / "build/dsp/fir_vectors/dsp2b.pass",
            "started": ROOT / "build/dsp/fir_vectors/dsp2b.started",
        },
    }
    selected = tests if args.test == "all" else {args.test: tests[args.test]}
    for test in selected.values():
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
            str(test["script"]),
        ]
        if args.open_waveform:
            vivado_command += ["-tclargs", "open-waveform"]
        try:
            run(vivado_command)
        except subprocess.CalledProcessError:
            # A started sentinel means HDL execution began and failed. Do not
            # hide that real test result behind a second simulator launch.
            if (
                args.open_waveform
                or test["started"].is_file()
                or not test["kernel"].is_file()
            ):
                raise
            print(
                "WARNING: Vivado could not launch XSim; trying the "
                "elaborated snapshot kernel",
                flush=True,
            )
            environment = os.environ.copy()
            vivado_library = settings.parent / "lib/lnx64.o"
            old_library_path = environment.get("LD_LIBRARY_PATH", "")
            environment["LD_LIBRARY_PATH"] = str(vivado_library) + (
                f":{old_library_path}" if old_library_path else ""
            )
            subprocess.run(
                [
                    str(test["kernel"]),
                    "--R",
                    "--testplusarg",
                    f"VECTOR_DIR={ROOT / 'build/dsp/fir_vectors'}",
                ],
                cwd=test["kernel"].parents[2],
                check=True,
                env=environment,
            )
            if not test["sentinel"].is_file():
                raise RuntimeError(
                    "snapshot kernel exited without the testbench PASS sentinel"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
