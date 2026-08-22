#!/usr/bin/env python3
"""Generate DSP-1 vectors and run the focused Verilator tests."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BUILD_DIR = ROOT / "build" / "verilator"
VECTOR_DIR = ROOT / "build" / "dsp" / "rtl_vectors"

TESTS = (
    (
        "qcrate_nco_tb",
        "rtl/tb/qcrate_nco_tb.sv",
        "rtl/dsp/qcrate_nco.sv",
    ),
    (
        "qcrate_ddc_mixer_tb",
        "rtl/tb/qcrate_ddc_mixer_tb.sv",
        "rtl/dsp/qcrate_ddc_mixer.sv",
    ),
    (
        "qcrate_dsp_frontend_tb",
        "rtl/tb/qcrate_dsp_frontend_tb.sv",
        "rtl/dsp/qcrate_nco.sv",
        "rtl/dsp/qcrate_ddc_mixer.sv",
        "rtl/dsp/qcrate_dsp_frontend.sv",
    ),
)


def run(command: list[str]) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True, env={
        **os.environ,
        "CCACHE_DISABLE": "1",
    })


def main() -> int:
    if shutil.which("verilator") is None:
        raise SystemExit("error: Verilator was not found in PATH")

    run([
        "python3",
        "host/dsp_model/qcrate_dsp.py",
        "generate-rtl-vectors",
        "host/dsp_model/configs/tone_1mhz.json",
        str(VECTOR_DIR),
        "--samples",
        "1024",
    ])

    for test in TESTS:
        top, *sources = test
        output_dir = BUILD_DIR / top
        run([
            "verilator",
            "--binary",
            "--timing",
            "-Wall",
            "-Wno-BLKSEQ",
            "--top-module",
            top,
            "--Mdir",
            str(output_dir),
            *sources,
        ])
        run([str(output_dir / f"V{top}")])

    print("PASS: all DSP-1 RTL tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
