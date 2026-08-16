#!/usr/bin/env python3
"""Generate or verify the tracked Q-Crate DSP lookup tables."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

import qcrate_dsp as dsp


MANIFEST = dsp.TABLE_DIR / "manifest.json"


def table_contents() -> dict[Path, str]:
    sine = dsp.generate_quarter_sine_lut()
    fir = dsp.quantize_fir_coefficients(dsp.generate_float_fir_coefficients())
    sine_text = dsp.render_hex(sine, dsp.ADC_BITS)
    fir_text = dsp.render_hex(fir, dsp.COEFFICIENT_BITS)
    manifest = {
        "format": "qcrate-dsp-tables-v1",
        "sample_rate_hz": dsp.SAMPLE_RATE_HZ,
        "decimation": dsp.DECIMATION,
        "phase_bits": dsp.PHASE_BITS,
        "lut_address_bits": dsp.LUT_ADDR_BITS,
        "quarter_lut_values": len(sine),
        "fir_estimated_taps": dsp.FIR_ESTIMATED_TAPS,
        "fir_taps": len(fir),
        "fir_cutoff_hz": dsp.FIR_CUTOFF_HZ,
        "passband_hz": dsp.PASSBAND_HZ,
        "stopband_hz": dsp.STOPBAND_HZ,
        "max_passband_ripple_db": 0.1,
        "min_stopband_attenuation_db": 70.0,
        "kaiser_beta": dsp.KAISER_BETA,
        "fir_integer_sum": int(fir.sum()),
        "sine_sha256": hashlib.sha256(sine_text.encode("ascii")).hexdigest(),
        "fir_sha256": hashlib.sha256(fir_text.encode("ascii")).hexdigest(),
    }
    return {
        dsp.SINE_TABLE: sine_text,
        dsp.FIR_TABLE: fir_text,
        MANIFEST: json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    }


def atomic_write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as stream:
            stream.write(contents)
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="fail instead of rewriting stale tables"
    )
    args = parser.parse_args()
    expected = table_contents()
    stale: list[Path] = []
    for path, contents in expected.items():
        try:
            actual = path.read_text(encoding="ascii")
        except FileNotFoundError:
            actual = ""
        if actual != contents:
            stale.append(path)

    if args.check:
        if stale:
            for path in stale:
                print(f"STALE {path}")
            return 1
        print("PASS DSP tables match their generator")
        return 0

    for path in stale:
        atomic_write(path, expected[path])
        print(f"wrote {path}")
    if not stale:
        print("DSP tables are already current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
