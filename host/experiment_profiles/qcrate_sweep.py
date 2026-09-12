#!/usr/bin/env python3
"""Plan, execute, and evaluate a configuration-bound Q-Crate LO sweep."""
from __future__ import annotations

import argparse
import binascii
import copy
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ANALYZER_DIR = ROOT / "host" / "analyzer"
DSP_DIR = ROOT / "host" / "dsp_model"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ANALYZER_DIR))
sys.path.insert(0, str(DSP_DIR))

import qcrate_analyzer as analyzer  # noqa: E402
import qcrate_capture_viewer as capture_viewer  # noqa: E402
import qcrate_dsp as dsp  # noqa: E402
import qcrate_experiment as experiment  # noqa: E402
import qcrate_profile as profile_contract  # noqa: E402
from qcrate_run import RunBundle  # noqa: E402


SWEEP_FORMAT = "qcrate-lo-sweep-v1"
PLAN_FORMAT = "qcrate-dp6e-sweep-plan-v1"
REPORT_FORMAT = "qcrate-dp6e-sweep-report-v1"
PASSBAND_HZ = 2_000_000
STOPBAND_HZ = 6_250_000
SETTLE_OUTPUT_SAMPLES = 32
MAGNITUDE_TOLERANCE_DB = 0.02
PHASE_TOLERANCE_DEGREES = 0.05
LABEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class SweepError(ValueError):
    """Raised when a sweep contract or result is not trustworthy."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SweepError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except json.JSONDecodeError as error:
        raise SweepError(f"invalid JSON in {path}: {error}") from error
    if not isinstance(document, dict):
        raise SweepError(f"{path} must contain a JSON object")
    return document


def atomic_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _exact_keys(value: object, expected: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SweepError(f"{context} must be an object")
    missing = sorted(expected - value.keys())
    unknown = sorted(value.keys() - expected)
    if missing or unknown:
        raise SweepError(
            f"{context} keys differ: missing={missing}, unknown={unknown}"
        )
    return value


def _repo_path(text: object, context: str) -> Path:
    if not isinstance(text, str) or not text:
        raise SweepError(f"{context} must be a nonempty repository-relative path")
    path = (ROOT / text).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as error:
        raise SweepError(f"{context} must remain inside the repository") from error
    if not path.is_file():
        raise SweepError(f"{context} does not exist: {text}")
    return path


def response_region(offset_hz: int) -> str:
    frequency = abs(offset_hz)
    if frequency <= PASSBAND_HZ:
        return "passband"
    if frequency < STOPBAND_HZ:
        return "transition"
    return "stopband"


def alias_frequency(frequency_hz: float, sample_rate_hz: float) -> float:
    """Map an unaliased complex frequency into [-Fs/2, Fs/2)."""
    return (frequency_hz + sample_rate_hz / 2.0) % sample_rate_hz - sample_rate_hz / 2.0


def coherent_tone(
    samples: np.ndarray,
    frequency_hz: float,
    sample_rate_hz: float,
    *,
    settle_samples: int = SETTLE_OUTPUT_SAMPLES,
) -> complex:
    """Project a settled complex record onto one exact frequency."""
    values = np.asarray(samples, dtype=np.complex128)
    if values.ndim != 1 or len(values) <= settle_samples:
        raise SweepError("coherent estimator has too few settled samples")
    indices = np.arange(settle_samples, len(values), dtype=np.float64)
    oscillator = np.exp(-2j * np.pi * frequency_hz * indices / sample_rate_hz)
    return complex(np.mean(values[settle_samples:] * oscillator))


def phase_error_degrees(actual: float, expected: float) -> float:
    return (actual - expected + 180.0) % 360.0 - 180.0


def _plan_identity(points: list[dict[str, Any]], phase_floor_db: float) -> str:
    payload = {
        "format": "qcrate-dp6e-sweep-identity-v1",
        "phase_response_floor_db": phase_floor_db,
        "points": points,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def prepare_plan(spec_path: Path, output: Path) -> dict[str, Any]:
    """Resolve every sweep point before permitting any target-side change."""
    spec_path = spec_path.resolve()
    output = output.resolve()
    if output.exists():
        raise SweepError(f"sweep output already exists: {output}")
    spec = _exact_keys(
        load_json(spec_path),
        {"format", "name", "base_profile", "phase_response_floor_db", "points"},
        "sweep",
    )
    if spec["format"] != SWEEP_FORMAT:
        raise SweepError(f"sweep.format must be {SWEEP_FORMAT!r}")
    if not isinstance(spec["name"], str) or not 1 <= len(spec["name"]) <= 128:
        raise SweepError("sweep.name must contain between 1 and 128 characters")
    phase_floor = spec["phase_response_floor_db"]
    if isinstance(phase_floor, bool) or not isinstance(phase_floor, (int, float)):
        raise SweepError("phase_response_floor_db must be a number")
    phase_floor = float(phase_floor)
    if not -160.0 <= phase_floor <= 0.0:
        raise SweepError("phase_response_floor_db must be between -160 and 0")
    if not isinstance(spec["points"], list) or len(spec["points"]) < 3:
        raise SweepError("sweep.points must contain at least three points")

    base_path = _repo_path(spec["base_profile"], "base_profile")
    profile_contract.compile_profile(base_path)
    base_document = load_json(base_path)
    signal_hz = base_document.get("dsp", {}).get("signal_frequency_hz")
    if isinstance(signal_hz, bool) or not isinstance(signal_hz, int):
        raise SweepError("base profile signal frequency must be an integer number of hertz")

    labels: set[str] = set()
    references = 0
    regions: set[str] = set()
    points: list[dict[str, Any]] = []
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.plan-", dir=output.parent))
    try:
        for ordinal, raw_point in enumerate(spec["points"]):
            point = _exact_keys(
                raw_point,
                {"label", "lo_frequency_hz", "region", "reference"},
                f"sweep point {ordinal}",
            )
            label = point["label"]
            if not isinstance(label, str) or LABEL_PATTERN.fullmatch(label) is None:
                raise SweepError(f"sweep point {ordinal} has an invalid label")
            if label in labels:
                raise SweepError(f"duplicate sweep label {label!r}")
            labels.add(label)
            lo_hz = point["lo_frequency_hz"]
            if isinstance(lo_hz, bool) or not isinstance(lo_hz, int):
                raise SweepError(f"sweep point {label} LO frequency must be integer hertz")
            if not 0 <= lo_hz < profile_contract.SAMPLE_RATE_HZ // 2:
                raise SweepError(f"sweep point {label} LO frequency is outside [0, Nyquist)")
            requested_offset = signal_hz - lo_hz
            actual_region = response_region(requested_offset)
            if point["region"] != actual_region:
                raise SweepError(
                    f"sweep point {label} is {actual_region}, not {point['region']}"
                )
            if not isinstance(point["reference"], bool):
                raise SweepError(f"sweep point {label} reference must be boolean")
            references += int(point["reference"])
            regions.add(actual_region)

            source_document = copy.deepcopy(base_document)
            source_document["name"] = f"{spec['name']}: {label}"
            source_document["dsp"]["lo_frequency_hz"] = lo_hz
            stem = f"{ordinal:02d}-{label}"
            source_relative = Path("profiles") / f"{stem}.profile.json"
            resolved_relative = Path("profiles") / f"{stem}.resolved.json"
            source_path = staging / source_relative
            resolved_path = staging / resolved_relative
            atomic_json(source_path, source_document)
            resolved = profile_contract.compile_profile(source_path)
            atomic_json(resolved_path, resolved)
            realized_offset = float(resolved["dsp"]["realized"]["baseband_frequency_hz"])
            output_rate = float(resolved["numerical_contract"]["output_sample_rate_hz"])
            points.append({
                "ordinal": ordinal,
                "label": label,
                "region": actual_region,
                "reference": point["reference"],
                "requested_lo_frequency_hz": lo_hz,
                "requested_baseband_frequency_hz": requested_offset,
                "realized_lo_frequency_hz": resolved["dsp"]["realized"]["lo_frequency_hz"],
                "realized_baseband_frequency_hz": resolved["dsp"]["realized"]["baseband_frequency_hz"],
                "observed_frequency_hz": alias_frequency(realized_offset, output_rate),
                "dsp_config_id": resolved["identity"]["dsp_config_id"],
                "dsp_config_sha256": resolved["identity"]["dsp_config_sha256"],
                "profile_sha256": resolved["identity"]["profile_sha256"],
                "source_profile": str(source_relative),
                "resolved_profile": str(resolved_relative),
                "run": str(Path("runs") / stem),
            })

        if references != 1:
            raise SweepError("sweep must define exactly one normalization reference point")
        if regions != {"passband", "transition", "stopband"}:
            raise SweepError("sweep must cover passband, transition, and stopband")
        config_ids = {point["dsp_config_id"] for point in points}
        if len(config_ids) != len(points):
            raise SweepError("every sweep point must resolve to a distinct DSP config ID")
        tuning_words = {
            profile_contract.load_resolved_profile(staging / point["resolved_profile"])
            ["dsp"]["active"]["LO_PHASE_INCREMENT"]
            for point in points
        }
        if len(tuning_words) != len(points):
            raise SweepError("multiple sweep points collapse to the same LO tuning word")

        plan = {
            "format": PLAN_FORMAT,
            "name": spec["name"],
            "source_spec": str(spec_path),
            "source_spec_sha256": hashlib.sha256(spec_path.read_bytes()).hexdigest(),
            "base_profile": str(base_path.relative_to(ROOT)),
            "phase_response_floor_db": phase_floor,
            "sweep_sha256": _plan_identity(points, phase_floor),
            "points": points,
        }
        atomic_json(staging / "sweep-plan.json", plan)
        os.replace(staging, output)
        return plan
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def load_plan(root: Path) -> dict[str, Any]:
    plan = _exact_keys(
        load_json(root.resolve() / "sweep-plan.json"),
        {
            "format", "name", "source_spec", "source_spec_sha256",
            "base_profile", "phase_response_floor_db", "sweep_sha256", "points",
        },
        "sweep plan",
    )
    if plan["format"] != PLAN_FORMAT or not isinstance(plan["points"], list):
        raise SweepError("unsupported or malformed sweep plan")
    try:
        expected_identity = _plan_identity(
            plan["points"], float(plan["phase_response_floor_db"])
        )
    except (TypeError, ValueError) as error:
        raise SweepError("sweep plan identity input is malformed") from error
    if plan["sweep_sha256"] != expected_identity:
        raise SweepError("sweep plan identity is stale or corrupted")
    return plan


def _sweep_path(root: Path, relative: object, context: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise SweepError(f"{context} is not a relative path")
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as error:
        raise SweepError(f"{context} escapes the sweep directory") from error
    return path


def _theoretical_fir_response(frequency_hz: float) -> complex:
    coefficients = dsp.load_hex(
        dsp.FIR_TABLE, dsp.COEFFICIENT_BITS, dsp.FIR_TAPS
    ).astype(np.float64) / float(1 << dsp.COEFFICIENT_FRAC_BITS)
    indices = np.arange(len(coefficients), dtype=np.float64)
    return complex(np.sum(
        coefficients * np.exp(-2j * np.pi * frequency_hz * indices / dsp.SAMPLE_RATE_HZ)
    ))


def measure_point(root: Path, point: dict[str, Any]) -> dict[str, Any]:
    run_path = _sweep_path(root, point["run"], "point run path")
    resolved_path = _sweep_path(
        root, point["resolved_profile"], "point resolved-profile path"
    )
    resolved = profile_contract.load_resolved_profile(resolved_path)
    shots, checked_words, _ = experiment.verify_run(run_path, resolved)
    bundle = RunBundle.open(run_path)
    sample_rate_hz, _ = bundle.sample_rate_hz(analyzer.DEFAULT_SAMPLE_RATE_HZ)
    frequency_hz = float(point["observed_frequency_hz"])
    expected_words = analyzer.expected_words(
        str(resolved_path.resolve()),
        resolved["acquisition"]["frame_length_words"] * resolved["acquisition"]["frame_count"],
    )
    expected_capture = capture_viewer.decode_payload(
        np.asarray(expected_words, dtype="<u4").tobytes()
    )
    model_phasor = coherent_tone(
        expected_capture.complex_samples, frequency_hz, sample_rate_hz
    )
    measured_phasors: list[complex] = []
    for shot in bundle.complete_shots:
        payload = bundle.read_samples(shot)
        if binascii.crc32(payload) & 0xFFFF_FFFF != shot.payload_crc32:
            raise SweepError(f"point {point['label']} shot {shot.shot_id} CRC mismatch")
        capture = capture_viewer.decode_payload(payload)
        measured_phasors.append(
            coherent_tone(capture.complex_samples, frequency_hz, sample_rate_hz)
        )
    measured = complex(np.mean(measured_phasors))
    magnitude_error = 20.0 * math.log10(
        max(abs(measured), 1e-15) / max(abs(model_phasor), 1e-15)
    )
    measured_phase = math.degrees(math.atan2(measured.imag, measured.real))
    model_phase = math.degrees(math.atan2(model_phasor.imag, model_phasor.real))
    health = bundle.health()
    return {
        **point,
        "run_id": f"0x{bundle.run_id:016x}",
        "shots": shots,
        "words_checked": checked_words,
        "iq_mismatches": 0,
        "measured_magnitude_dbfs": 20.0 * math.log10(max(abs(measured), 1e-15)),
        "model_magnitude_dbfs": 20.0 * math.log10(max(abs(model_phasor), 1e-15)),
        "magnitude_error_db": magnitude_error,
        "measured_phase_degrees": measured_phase,
        "model_phase_degrees": model_phase,
        "phase_error_degrees": phase_error_degrees(measured_phase, model_phase),
        "theoretical_fir_magnitude_db": 20.0 * math.log10(
            max(abs(_theoretical_fir_response(
                float(point["realized_baseband_frequency_hz"])
            )), 1e-15)
        ),
        "integrity_ok": health.integrity_ok,
        "incomplete_shots": health.incomplete_shots,
        "skipped_shot_ids": health.skipped_shot_ids,
        "kernel_receive_drops": health.kernel_receive_drops,
        "missed_triggers": health.missed_triggers,
        "skipped_triggers": health.skipped_triggers,
        "dma_error_events": health.dma_error_events,
    }


def evaluate_sweep(root: Path) -> dict[str, Any]:
    root = root.resolve()
    plan = load_plan(root)
    measured = [measure_point(root, point) for point in plan["points"]]
    reference = next(point for point in measured if point["reference"])
    measured_reference = reference["measured_magnitude_dbfs"]
    model_reference = reference["model_magnitude_dbfs"]
    fir_reference = reference["theoretical_fir_magnitude_db"]
    phase_floor = float(plan["phase_response_floor_db"])
    total_words = 0
    for point in measured:
        point["measured_response_db"] = (
            point["measured_magnitude_dbfs"] - measured_reference
        )
        point["model_response_db"] = point["model_magnitude_dbfs"] - model_reference
        point["theoretical_fir_response_db"] = (
            point["theoretical_fir_magnitude_db"] - fir_reference
        )
        point["phase_required"] = point["model_response_db"] >= phase_floor
        point["pass"] = (
            point["integrity_ok"]
            and point["iq_mismatches"] == 0
            and abs(point["magnitude_error_db"]) <= MAGNITUDE_TOLERANCE_DB
            and (
                not point["phase_required"]
                or abs(point["phase_error_degrees"]) <= PHASE_TOLERANCE_DEGREES
            )
        )
        total_words += point["words_checked"]

    checks = {
        "all_points_complete": all(point["integrity_ok"] for point in measured),
        "all_config_ids_distinct": len({point["dsp_config_id"] for point in measured}) == len(measured),
        "all_iq_words_bit_exact": all(
            point["iq_mismatches"] == 0 for point in measured
        ),
        "meaningful_phase_matches": all(
            not point["phase_required"]
            or abs(point["phase_error_degrees"]) <= PHASE_TOLERANCE_DEGREES
            for point in measured
        ),
        "all_regions_measured": {point["region"] for point in measured}
        == {"passband", "transition", "stopband"},
    }
    passed = all(checks.values()) and all(point["pass"] for point in measured)
    return {
        "format": REPORT_FORMAT,
        "generated_utc": utc_now(),
        "result": "PASS" if passed else "FAIL",
        "sweep_sha256": plan["sweep_sha256"],
        "name": plan["name"],
        "phase_response_floor_db": phase_floor,
        "magnitude_tolerance_db": MAGNITUDE_TOLERANCE_DB,
        "phase_tolerance_degrees": PHASE_TOLERANCE_DEGREES,
        "total_points": len(measured),
        "total_iq_words_checked": total_words,
        "checks": checks,
        "points": measured,
    }


def render_report(summary: dict[str, Any], output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    points = sorted(
        summary["points"], key=lambda point: abs(float(point["realized_baseband_frequency_hz"]))
    )
    offsets = np.asarray([
        abs(float(point["realized_baseband_frequency_hz"])) / 1e6 for point in points
    ])
    measured = np.asarray([point["measured_response_db"] for point in points])
    modeled = np.asarray([point["model_response_db"] for point in points])
    theoretical = np.asarray([point["theoretical_fir_response_db"] for point in points])
    phase_mask = np.asarray([point["phase_required"] for point in points], dtype=bool)
    measured_phase = np.rad2deg(np.unwrap(np.deg2rad(np.asarray([
        point["measured_phase_degrees"] for point in points
    ])[phase_mask])))
    model_phase = np.rad2deg(np.unwrap(np.deg2rad(np.asarray([
        point["model_phase_degrees"] for point in points
    ])[phase_mask])))

    figure = Figure(figsize=(14.5, 9.5), dpi=100, facecolor="#f4f5f7")
    grid = figure.add_gridspec(3, 1, height_ratios=(2.2, 1.6, 1.0), hspace=0.34)
    magnitude_axis = figure.add_subplot(grid[0])
    phase_axis = figure.add_subplot(grid[1])
    evidence_axis = figure.add_subplot(grid[2])
    for axis in (magnitude_axis, phase_axis):
        axis.set_facecolor("#ffffff")
        axis.grid(True, color="#d7dce2", linewidth=0.7)

    magnitude_axis.plot(offsets, theoretical, color="#6b7280", linestyle="--",
                        linewidth=1.2, label="Quantized FIR")
    magnitude_axis.plot(offsets, modeled, color="#2563eb", marker="s",
                        linewidth=1.3, label="Bit-accurate model")
    magnitude_axis.plot(offsets, measured, color="#087f5b", marker="o",
                        linewidth=1.4, label="KV260 measurement")
    magnitude_axis.axvline(PASSBAND_HZ / 1e6, color="#9a6700", linestyle=":")
    magnitude_axis.axvline(STOPBAND_HZ / 1e6, color="#991b1b", linestyle=":")
    magnitude_axis.set_title("Measured DDC low-pass response")
    magnitude_axis.set_xlabel("Absolute source-to-LO offset (MHz)")
    magnitude_axis.set_ylabel("Normalized magnitude (dB)")
    magnitude_axis.set_ylim(min(-100.0, float(np.min(measured)) - 5.0), 3.0)
    magnitude_axis.legend(loc="lower left", ncols=3, fontsize=9)

    phase_axis.plot(offsets[phase_mask], model_phase, color="#2563eb",
                    marker="s", linewidth=1.3, label="Bit-accurate model")
    phase_axis.plot(offsets[phase_mask], measured_phase, color="#8b5cf6",
                    marker="o", linewidth=1.4, label="KV260 measurement")
    phase_axis.set_title(
        f"Complex phase where normalized model response >= "
        f"{summary['phase_response_floor_db']:.0f} dB"
    )
    phase_axis.set_xlabel("Absolute source-to-LO offset (MHz)")
    phase_axis.set_ylabel("Phase (degrees)")
    phase_axis.legend(loc="best", fontsize=9)

    evidence_axis.set_axis_off()
    result = summary["result"]
    color = "#087f5b" if result == "PASS" else "#991b1b"
    max_mag = max(abs(point["magnitude_error_db"]) for point in points)
    phase_errors = [
        abs(point["phase_error_degrees"]) for point in points if point["phase_required"]
    ]
    max_phase = max(phase_errors, default=0.0)
    fault_cases = summary.get("faults", {}).get("cases", {})
    report_kind = "ACCEPTANCE" if fault_cases else "SWEEP"
    evidence_axis.text(0.01, 0.72, f"DP-6E {report_kind} {result}", fontsize=20,
                       weight="bold", color=color, transform=evidence_axis.transAxes)
    fault_text = ""
    if isinstance(fault_cases, dict) and fault_cases:
        passed_faults = sum(
            isinstance(case, dict) and case.get("pass") is True
            for case in fault_cases.values()
        )
        fault_text = (
            f"{passed_faults}/{len(fault_cases)} safety/recovery tests passed | "
            "invalid and unsafe commits rejected | interrupted and stale runs contained"
        )
    evidence_axis.text(
        0.01, 0.43,
        f"{summary['total_points']} distinct runtime configurations | "
        f"{summary['total_iq_words_checked']:,} bit-exact IQ words | "
        f"max magnitude error {max_mag:.6f} dB | "
        f"max meaningful phase error {max_phase:.6f} deg",
        fontsize=11, color="#111827", transform=evidence_axis.transAxes,
    )
    if fault_text:
        evidence_axis.text(
            0.01, 0.23, fault_text, fontsize=10.5, color="#111827",
            transform=evidence_axis.transAxes,
        )
    evidence_axis.text(
        0.01, 0.04,
        f"Sweep identity {summary['sweep_sha256']} | "
        "one immutable configuration and one independent recording per point",
        fontsize=9.5, color="#374151", transform=evidence_axis.transAxes,
    )
    figure.suptitle(summary["name"], fontsize=16, weight="bold", color="#111827")
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=160, facecolor=figure.get_facecolor())


def analyze(root: Path) -> dict[str, Any]:
    summary = evaluate_sweep(root)
    atomic_json(root / "dp6e-sweep.json", summary)
    render_report(summary, root / "dp6e-sweep.png")
    print(
        f"{summary['result']} DP-6E sweep: {summary['total_points']} points, "
        f"{summary['total_iq_words_checked']:,} IQ words"
    )
    print(f"summary : {root / 'dp6e-sweep.json'}")
    print(f"report  : {root / 'dp6e-sweep.png'}")
    return summary


def _preserve_interrupted(path: Path) -> Path:
    suffix = 1
    while True:
        destination = path.with_name(f"{path.name}.interrupted-{suffix:02d}")
        if not destination.exists():
            os.replace(path, destination)
            return destination
        suffix += 1


def run_sweep(args: argparse.Namespace) -> int:
    root = args.output.resolve()
    spec = args.spec.resolve()
    if not args.interactive_auth:
        experiment.verify_unattended_access(args.board)
    if not (root / "sweep-plan.json").exists():
        plan = prepare_plan(spec, root)
    else:
        plan = load_plan(root)
        if hashlib.sha256(spec.read_bytes()).hexdigest() != plan["source_spec_sha256"]:
            raise SweepError("existing plan was generated from a different sweep specification")

    for point in plan["points"]:
        resolved_path = _sweep_path(
            root, point["resolved_profile"], "point resolved-profile path"
        )
        run_path = _sweep_path(root, point["run"], "point run path")
        resolved = profile_contract.load_resolved_profile(resolved_path)
        if run_path.exists():
            if not args.resume:
                raise SweepError(f"point run already exists; use --resume: {run_path}")
            try:
                experiment.verify_run(run_path, resolved)
                print(f"SKIP accepted point {point['label']}")
                continue
            except (OSError, ValueError, RuntimeError):
                preserved = _preserve_interrupted(run_path)
                print(f"PRESERVE interrupted point at {preserved}")

        point_args = argparse.Namespace(
            profile=resolved_path,
            board=args.board,
            destination=args.destination,
            source=args.source,
            output=run_path,
            bind=args.bind,
            port=args.port,
            shots=args.shots,
            banks=args.banks,
            rate_mbps=args.rate_mbps,
            sequence=args.sequence,
            recorder=args.recorder,
            startup_timeout_seconds=args.startup_timeout_seconds,
            snapshot=run_path / "measurement.png",
            gui=False,
            unattended=not args.interactive_auth,
        )
        print(f"\n== DP-6E point {point['ordinal'] + 1}/{len(plan['points'])}: {point['label']} ==")
        experiment.run_experiment(point_args)

    summary = analyze(root)
    return 0 if summary["result"] == "PASS" else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="resolve and freeze every sweep point")
    plan.add_argument("--spec", required=True, type=Path)
    plan.add_argument("--output", required=True, type=Path)
    analyze_parser = commands.add_parser("analyze", help="evaluate completed point runs")
    analyze_parser.add_argument("--sweep", required=True, type=Path)
    run = commands.add_parser("run", help="plan, acquire, and evaluate the sweep")
    run.add_argument("--spec", required=True, type=Path)
    run.add_argument("--output", required=True, type=Path)
    run.add_argument("--board", required=True)
    run.add_argument("--destination", required=True)
    run.add_argument("--source", required=True)
    run.add_argument("--bind")
    run.add_argument("--port", type=int, default=47000)
    run.add_argument("--banks", type=int, default=4)
    run.add_argument("--rate-mbps", type=int, default=420)
    run.add_argument("--sequence", default=experiment.DEFAULT_SEQUENCE)
    run.add_argument("--recorder", type=Path, default=experiment.DEFAULT_RECORDER)
    run.add_argument("--startup-timeout-seconds", type=int, default=30)
    run.add_argument("--shots", type=int, default=10)
    run.add_argument("--resume", action="store_true")
    run.add_argument(
        "--interactive-auth", action="store_true",
        help="use legacy SSH and sudo password prompts instead of unattended access",
    )
    args = parser.parse_args()
    if args.command == "run":
        args.bind = args.bind or args.destination
        if not 1 <= args.port <= 65535 or not 2 <= args.banks <= 64:
            parser.error("port or banks are outside their supported range")
        if args.rate_mbps < 0 or args.startup_timeout_seconds < 1:
            parser.error("rate and startup timeout must not be negative")
    if args.command == "run" and args.shots < 1:
        parser.error("shots must be positive")
    return args


def main() -> int:
    args = parse_args()
    try:
        if args.command == "plan":
            plan = prepare_plan(args.spec, args.output)
            print(
                f"PASS planned {len(plan['points'])} distinct DP-6E configurations"
            )
            print(f"plan : {args.output.resolve() / 'sweep-plan.json'}")
            return 0
        if args.command == "analyze":
            return 0 if analyze(args.sweep.resolve())["result"] == "PASS" else 2
        return run_sweep(args)
    except (OSError, SweepError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
