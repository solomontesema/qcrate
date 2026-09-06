#!/usr/bin/env python3
"""Compile Q-Crate experiment profiles into deterministic integer contracts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any


PROFILE_FORMAT = "qcrate-experiment-profile-v1"
RESOLVED_FORMAT = "qcrate-experiment-resolved-v1"
DSP_IDENTITY_FORMAT = "qcrate-dsp-config-identity-v1"
PROFILE_IDENTITY_FORMAT = "qcrate-experiment-identity-v1"
SAMPLE_RATE_HZ = 200_000_000
DECIMATION = 16
PHASE_BITS = 32
PHASE_MODULUS = 1 << PHASE_BITS
REPO_ROOT = Path(__file__).resolve().parents[2]
TABLE_MANIFEST = REPO_ROOT / "rtl" / "dsp" / "tables" / "manifest.json"
REGISTER_CONTRACT = (
    REPO_ROOT / "common" / "config" / "qcrate_dsp_registers.json"
)
SEQUENCE_COMPILER_DIR = REPO_ROOT / "host" / "sequence_compiler"


class ProfileError(ValueError):
    """Raised when an experiment profile cannot be resolved unambiguously."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProfileError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as stream:
            document = json.load(
                stream,
                object_pairs_hook=_reject_duplicate_keys,
                parse_float=Decimal,
            )
    except json.JSONDecodeError as error:
        raise ProfileError(f"invalid JSON in {path}: {error}") from error
    if not isinstance(document, dict):
        raise ProfileError(f"{path} must contain a JSON object")
    return document


def _check_keys(value: Any, *, required: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProfileError(f"{context} must be an object")
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - required)
    if missing or unknown:
        raise ProfileError(
            f"{context} keys differ: missing={missing}, unknown={unknown}"
        )
    return value


def _integer(value: Any, *, minimum: int, maximum: int, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProfileError(f"{context} must be an integer")
    if not minimum <= value <= maximum:
        raise ProfileError(f"{context} must be between {minimum} and {maximum}")
    return value


def _decimal(value: Any, *, minimum: Decimal, maximum: Decimal,
             maximum_inclusive: bool, context: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise ProfileError(f"{context} must be a JSON number")
    result = Decimal(value)
    upper_ok = result <= maximum if maximum_inclusive else result < maximum
    if result < minimum or not upper_ok:
        relation = "at most" if maximum_inclusive else "less than"
        raise ProfileError(
            f"{context} must be at least {minimum} and {relation} {maximum}"
        )
    return result


def _decimal_string(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _phase_word(value: Decimal, scale: int) -> int:
    scaled = value * PHASE_MODULUS / scale
    return int(scaled.to_integral_value(rounding=ROUND_HALF_UP)) & 0xFFFF_FFFF


def _q1_15(value: Decimal) -> int:
    scaled = int((value * (1 << 15)).to_integral_value(rounding=ROUND_HALF_UP))
    return min(max(scaled, -0x8000), 0x7FFF)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _resolve_sequence(source: str) -> dict[str, Any]:
    source_path = (REPO_ROOT / source).resolve()
    try:
        source_path.relative_to(REPO_ROOT)
    except ValueError as error:
        raise ProfileError("sequence.source must remain inside the repository") from error
    if not source_path.is_file():
        raise ProfileError(f"sequence source does not exist: {source}")

    sys.path.insert(0, str(SEQUENCE_COMPILER_DIR))
    try:
        import qcrate_sequence as sequence

        events = sequence.compile_document(sequence.load_json(source_path))
        image = sequence.encode_sequence(events)
    except (OSError, ValueError) as error:
        raise ProfileError(f"invalid sequence {source}: {error}") from error
    finally:
        sys.path.pop(0)

    payload = image[sequence.HEADER.size:]
    import zlib

    return {
        "source": source,
        "image_sha256": hashlib.sha256(image).hexdigest(),
        "payload_crc32": zlib.crc32(payload) & 0xFFFF_FFFF,
        "event_count": len(events),
        "tick_hz": sequence.TICK_HZ,
    }


def _load_contracts() -> tuple[dict[str, Any], dict[str, Any]]:
    tables = _load_json(TABLE_MANIFEST)
    registers = _load_json(REGISTER_CONTRACT)
    if tables.get("format") != "qcrate-dsp-tables-v1":
        raise ProfileError("unsupported DSP table manifest")
    if tables.get("sample_rate_hz") != SAMPLE_RATE_HZ:
        raise ProfileError("DSP table sample rate differs from the profile contract")
    if tables.get("decimation") != DECIMATION:
        raise ProfileError("DSP table decimation differs from the profile contract")
    if registers.get("format") != "qcrate-dsp-register-contract-v1":
        raise ProfileError("unsupported DSP register contract")
    return tables, registers


def _numerical_contract(tables: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": "qcrate-dsp-v1",
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "output_sample_rate_hz": SAMPLE_RATE_HZ // DECIMATION,
        "decimation": DECIMATION,
        "phase_bits": PHASE_BITS,
        "amplitude_format": "signed-q1.15",
        "mixer_format": "signed-q1.17",
        "coefficient_format": "signed-q1.17",
        "output_format": "complex-signed-q1.15",
        "rounding": "nearest-ties-away-from-zero",
        "overflow": "signed-saturation",
        "fir_taps": tables["fir_taps"],
        "fir_sha256": tables["fir_sha256"],
        "sine_sha256": tables["sine_sha256"],
    }


def _register_writes(active: dict[str, int], config_id: int,
                     registers: dict[str, Any]) -> list[dict[str, Any]]:
    values = {
        "CONFIG_ID_LOW": config_id & 0xFFFF_FFFF,
        "CONFIG_ID_HIGH": config_id >> 32,
        **active,
    }
    writes: list[dict[str, Any]] = []
    page = registers["page_offset"]
    for field in registers["configuration_fields"]:
        name = field["name"]
        value = values[name]
        if not 0 <= value < (1 << field["width"]):
            raise ProfileError(f"resolved {name} does not fit {field['width']} bits")
        writes.append(
            {
                "name": name,
                "offset": page + field["shadow_offset"],
                "value": value,
            }
        )
    return writes


def compile_profile(path: Path) -> dict[str, Any]:
    """Resolve one human profile into exact hardware and identity values."""
    document = _check_keys(
        _load_json(path),
        required={"format", "name", "dsp", "acquisition", "sequence"},
        context="profile",
    )
    if document["format"] != PROFILE_FORMAT:
        raise ProfileError(f"profile.format must be {PROFILE_FORMAT!r}")
    if not isinstance(document["name"], str) or not 1 <= len(document["name"]) <= 128:
        raise ProfileError("profile.name must contain between 1 and 128 characters")

    dsp_source = _check_keys(
        document["dsp"],
        required={
            "signal_frequency_hz",
            "signal_phase_turns",
            "signal_amplitude",
            "noise_amplitude",
            "noise_seed",
            "lo_frequency_hz",
            "lo_phase_turns",
        },
        context="dsp",
    )
    nyquist = Decimal(SAMPLE_RATE_HZ) / 2
    signal_frequency = _decimal(
        dsp_source["signal_frequency_hz"], minimum=Decimal(0), maximum=nyquist,
        maximum_inclusive=False, context="dsp.signal_frequency_hz"
    )
    lo_frequency = _decimal(
        dsp_source["lo_frequency_hz"], minimum=Decimal(0), maximum=nyquist,
        maximum_inclusive=False, context="dsp.lo_frequency_hz"
    )
    signal_phase = _decimal(
        dsp_source["signal_phase_turns"], minimum=Decimal(0), maximum=Decimal(1),
        maximum_inclusive=False, context="dsp.signal_phase_turns"
    )
    lo_phase = _decimal(
        dsp_source["lo_phase_turns"], minimum=Decimal(0), maximum=Decimal(1),
        maximum_inclusive=False, context="dsp.lo_phase_turns"
    )
    signal_amplitude = _decimal(
        dsp_source["signal_amplitude"], minimum=Decimal(0), maximum=Decimal(1),
        maximum_inclusive=True, context="dsp.signal_amplitude"
    )
    noise_amplitude = _decimal(
        dsp_source["noise_amplitude"], minimum=Decimal(0), maximum=Decimal(1),
        maximum_inclusive=True, context="dsp.noise_amplitude"
    )
    if signal_amplitude + noise_amplitude > 1:
        raise ProfileError("signal plus noise amplitude must not exceed 1")
    noise_seed = _integer(
        dsp_source["noise_seed"], minimum=1, maximum=0xFFFF,
        context="dsp.noise_seed"
    )

    acquisition_source = _check_keys(
        document["acquisition"],
        required={"frame_length_words", "frame_count", "stream_mode"},
        context="acquisition",
    )
    frame_length = _integer(
        acquisition_source["frame_length_words"], minimum=1, maximum=262_144,
        context="acquisition.frame_length_words"
    )
    frame_count = _integer(
        acquisition_source["frame_count"], minimum=1, maximum=255,
        context="acquisition.frame_count"
    )
    if frame_length * frame_count > 1_000_000:
        raise ProfileError("acquisition contains more than 1000000 output words")
    if acquisition_source["stream_mode"] != "dsp":
        raise ProfileError("DP-6 profile stream_mode must be 'dsp'")
    acquisition = {
        "frame_length_words": frame_length,
        "frame_count": frame_count,
        "stream_mode": "dsp",
        "stream_mode_value": 1,
    }

    sequence_source = _check_keys(
        document["sequence"], required={"source"}, context="sequence"
    )
    if not isinstance(sequence_source["source"], str) or not sequence_source["source"]:
        raise ProfileError("sequence.source must be a nonempty repository-relative path")
    sequence = _resolve_sequence(sequence_source["source"])
    tables, registers = _load_contracts()
    numerical_contract = _numerical_contract(tables)

    requested = {
        "signal_frequency_hz": _decimal_string(signal_frequency),
        "signal_phase_turns": _decimal_string(signal_phase),
        "signal_amplitude": _decimal_string(signal_amplitude),
        "noise_amplitude": _decimal_string(noise_amplitude),
        "noise_seed": noise_seed,
        "lo_frequency_hz": _decimal_string(lo_frequency),
        "lo_phase_turns": _decimal_string(lo_phase),
    }
    active = {
        "SIGNAL_PHASE_INCREMENT": _phase_word(signal_frequency, SAMPLE_RATE_HZ),
        "SIGNAL_PHASE_INITIAL": _phase_word(signal_phase, 1),
        "SIGNAL_AMPLITUDE_Q1_15": _q1_15(signal_amplitude),
        "NOISE_AMPLITUDE_Q1_15": _q1_15(noise_amplitude),
        "NOISE_SEED": noise_seed,
        "LO_PHASE_INCREMENT": _phase_word(lo_frequency, SAMPLE_RATE_HZ),
        "LO_PHASE_INITIAL": _phase_word(lo_phase, 1),
    }
    dsp_identity_payload = {
        "format": DSP_IDENTITY_FORMAT,
        "numerical_contract": numerical_contract,
        "active": active,
    }
    dsp_digest = _sha256(dsp_identity_payload)
    config_id = int(dsp_digest[:16], 16)
    profile_identity_payload = {
        "format": PROFILE_IDENTITY_FORMAT,
        "dsp_config_sha256": dsp_digest,
        "acquisition": acquisition,
        "sequence": {
            "image_sha256": sequence["image_sha256"],
            "event_count": sequence["event_count"],
            "tick_hz": sequence["tick_hz"],
        },
    }
    profile_digest = _sha256(profile_identity_payload)

    realized_signal = Decimal(active["SIGNAL_PHASE_INCREMENT"]) * SAMPLE_RATE_HZ / PHASE_MODULUS
    realized_lo = Decimal(active["LO_PHASE_INCREMENT"]) * SAMPLE_RATE_HZ / PHASE_MODULUS
    return {
        "format": RESOLVED_FORMAT,
        "name": document["name"],
        "identity": {
            "profile_sha256": profile_digest,
            "dsp_config_sha256": dsp_digest,
            "dsp_config_id": f"0x{config_id:016x}",
        },
        "numerical_contract": numerical_contract,
        "dsp": {
            "requested": requested,
            "active": active,
            "realized": {
                "signal_frequency_hz": _decimal_string(realized_signal),
                "lo_frequency_hz": _decimal_string(realized_lo),
                "baseband_frequency_hz": _decimal_string(realized_signal - realized_lo),
            },
        },
        "acquisition": acquisition,
        "sequence": sequence,
        "register_writes": _register_writes(active, config_id, registers),
    }


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary_name, path)
    except BaseException:
        os.unlink(temporary_name)
        raise


def _model_peak(resolved: dict[str, Any]) -> tuple[float, float]:
    sys.path.insert(0, str(REPO_ROOT / "host" / "dsp_model"))
    try:
        import numpy as np
        import qcrate_dsp as dsp

        config = dsp.config_from_resolved_document(resolved)
        result = dsp.run_model(config)
    finally:
        sys.path.pop(0)
    settled = min(32, len(result.fixed_i) // 8)
    iq = result.fixed_i[settled:].astype(np.complex128)
    iq += 1j * result.fixed_q[settled:].astype(np.float64)
    windowed = iq * np.hanning(len(iq))
    spectrum = np.fft.fft(windowed)
    frequencies = np.fft.fftfreq(
        len(iq), 1.0 / (SAMPLE_RATE_HZ / DECIMATION)
    )
    peak_hz = float(frequencies[int(np.argmax(np.abs(spectrum)))])
    bin_hz = SAMPLE_RATE_HZ / DECIMATION / len(iq)
    return peak_hz, bin_hz


def prove_profiles(paths: list[Path]) -> dict[str, Any]:
    if len(paths) < 2:
        raise ProfileError("proof requires at least two profiles")
    results: list[dict[str, Any]] = []
    for path in paths:
        resolved = compile_profile(path)
        peak_hz, bin_hz = _model_peak(resolved)
        predicted_hz = float(Decimal(resolved["dsp"]["realized"]["baseband_frequency_hz"]))
        if abs(peak_hz - predicted_hz) > bin_hz:
            raise ProfileError(
                f"{path}: model peak {peak_hz} Hz differs from "
                f"resolved prediction {predicted_hz} Hz"
            )
        results.append(
            {
                "profile": str(path.relative_to(REPO_ROOT)),
                "profile_sha256": resolved["identity"]["profile_sha256"],
                "dsp_config_id": resolved["identity"]["dsp_config_id"],
                "lo_phase_increment": resolved["dsp"]["active"]["LO_PHASE_INCREMENT"],
                "predicted_baseband_hz": predicted_hz,
                "model_peak_hz": peak_hz,
                "fft_bin_hz": bin_hz,
            }
        )
    if len({item["dsp_config_id"] for item in results}) != len(results):
        raise ProfileError("proof profiles did not produce distinct DSP config IDs")
    if len({item["lo_phase_increment"] for item in results}) != len(results):
        raise ProfileError("proof profiles did not produce distinct LO tuning words")
    return {"format": "qcrate-dp6a-proof-v1", "pass": True, "profiles": results}


def _print_resolved(document: dict[str, Any]) -> None:
    active = document["dsp"]["active"]
    print(f"profile SHA-256 : {document['identity']['profile_sha256']}")
    print(f"DSP config ID   : {document['identity']['dsp_config_id']}")
    print(f"signal tuning   : 0x{active['SIGNAL_PHASE_INCREMENT']:08x}")
    print(f"LO tuning       : 0x{active['LO_PHASE_INCREMENT']:08x}")
    print(f"baseband        : {document['dsp']['realized']['baseband_frequency_hz']} Hz")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    compile_parser = subparsers.add_parser("compile", help="write a resolved profile")
    compile_parser.add_argument("profile", type=Path)
    compile_parser.add_argument("output", type=Path)
    show_parser = subparsers.add_parser("show", help="resolve and summarize a profile")
    show_parser.add_argument("profile", type=Path)
    prove_parser = subparsers.add_parser(
        "prove", help="compare resolved profiles with the bit-accurate DSP model"
    )
    prove_parser.add_argument("profiles", nargs="+", type=Path)
    prove_parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        if args.command == "prove":
            proof = prove_profiles([path.resolve() for path in args.profiles])
            if args.output:
                _write_json(args.output, proof)
            for result in proof["profiles"]:
                print(
                    f"{result['dsp_config_id']}  LO=0x{result['lo_phase_increment']:08x}  "
                    f"predicted={result['predicted_baseband_hz']:.3f} Hz  "
                    f"model={result['model_peak_hz']:.3f} Hz"
                )
            print("PASS distinct resolved configurations produced the predicted baseband shifts")
            return 0

        resolved = compile_profile(args.profile.resolve())
        _print_resolved(resolved)
        if args.command == "compile":
            _write_json(args.output.resolve(), resolved)
            print(f"wrote            : {args.output}")
    except (OSError, ProfileError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
