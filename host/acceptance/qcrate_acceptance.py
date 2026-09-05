#!/usr/bin/env python3
"""Run and evaluate Q-Crate DP-5D instrument acceptance."""
from __future__ import annotations

import argparse
import binascii
import datetime as dt
import json
import os
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ANALYZER_DIR = ROOT / "host" / "analyzer"
sys.path.insert(0, str(ANALYZER_DIR))

import qcrate_analyzer as analyzer  # noqa: E402
from qcrate_run import RunBundle  # noqa: E402


FORMAT = "qcrate-dp5d-acceptance-v1"
FAULT_FORMAT = "qcrate-dp5d-faults-v1"
REQUIRED_FAULTS = (
    "receiver_restart",
    "analyzer_restart",
    "ethernet_disconnect",
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def atomic_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def nested(mapping: object, *keys: str, default: Any = 0) -> Any:
    value = mapping
    for key in keys:
        if not isinstance(value, dict):
            return default
        value = value.get(key, default)
    return value


def evaluate_run(
    run_path: Path,
    *,
    minimum_duration: float,
    faults_path: Path | None,
) -> dict[str, Any]:
    bundle = RunBundle.open(run_path)
    health = bundle.health()
    sender = bundle.sender_report
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, actual: Any, requirement: str) -> None:
        checks.append({
            "name": name,
            "pass": bool(passed),
            "actual": actual,
            "requirement": requirement,
        })

    sender_duration = float(nested(
        sender, "timing", "capture_duration_seconds", default=0.0
    ))
    sender_shots = int(nested(sender, "capture", "shots_transmitted", default=-1))
    check("recorder_complete", bundle.run_complete is True, bundle.run_complete,
          "run.json complete must be true")
    check("sender_report", sender is not None, sender is not None,
          "sender.json must be present and identity-matched")
    check("sender_complete", bool(sender and sender.get("complete")),
          None if sender is None else sender.get("complete"),
          "qcrate-streamer must close the run cleanly")
    check("capture_duration", sender_duration >= minimum_duration,
          round(sender_duration, 6), f">= {minimum_duration:.3f} seconds")
    check("complete_shots", health.complete_shots > 0, health.complete_shots,
          "> 0")
    check("incomplete_shots", health.incomplete_shots == 0,
          health.incomplete_shots, "0")
    check("shot_identity_continuity", health.skipped_shot_ids == 0,
          health.skipped_shot_ids, "0 skipped IDs")
    check("sender_recorder_shot_count", sender_shots == health.complete_shots,
          {"sender": sender_shots, "recorder": health.complete_shots}, "equal")
    check("record_integrity", health.record_integrity_ok, {
        "missing_packets": health.missing_packets,
        "malformed_packets": health.malformed_packets,
        "conflicting_packets": health.conflicting_packets,
        "kernel_receive_drops": health.kernel_receive_drops,
        "continuity_errors": health.continuity_errors,
    }, "all zero")
    check("acquisition_integrity", health.acquisition_integrity_ok, {
        "missed_triggers": health.missed_triggers,
        "skipped_triggers": health.skipped_triggers,
        "dma_error_events": health.dma_error_events,
    }, "all zero")

    timestamps = [shot.first_sample_timestamp for shot in bundle.complete_shots]
    timestamp_monotonic = all(
        right > left for left, right in zip(timestamps, timestamps[1:])
    )
    check("hardware_timestamps", bool(timestamps) and timestamp_monotonic,
          {"first": timestamps[0] if timestamps else None,
           "last": timestamps[-1] if timestamps else None},
          "present and strictly increasing")

    expected_config = analyzer.current_config_id()
    reference_words: np.ndarray | None = None
    checked_words = 0
    reference_mismatches = 0
    crc_mismatches = 0
    unexpected_configs: set[int] = set()
    for shot in bundle.complete_shots:
        payload = bundle.read_samples(shot)
        if (binascii.crc32(payload) & 0xFFFFFFFF) != shot.payload_crc32:
            crc_mismatches += 1
        if shot.config_id != expected_config:
            unexpected_configs.add(shot.config_id)
            continue
        words = np.frombuffer(payload, dtype="<u4")
        if reference_words is None or len(reference_words) != len(words):
            reference_words = analyzer.expected_words(
                str(analyzer.DEFAULT_CONFIG.resolve()), len(words)
            )
        reference_mismatches += int(np.count_nonzero(words != reference_words))
        checked_words += len(words)
    check("published_payload_crc", crc_mismatches == 0, crc_mismatches,
          "0 mismatched shot payloads")
    check("bit_exact_iq", not unexpected_configs and reference_mismatches == 0,
          {"words_checked": checked_words,
           "mismatches": reference_mismatches,
           "unexpected_config_ids": [f"0x{value:016x}"
                                     for value in sorted(unexpected_configs)]},
          "every published IQ word matches the canonical model")

    clean_pass = all(item["pass"] for item in checks)
    fault_document: dict[str, Any] | None = None
    fault_results: dict[str, Any] = {}
    if faults_path is not None and faults_path.exists():
        fault_document = json.loads(faults_path.read_text(encoding="utf-8"))
        if fault_document.get("format") != FAULT_FORMAT:
            raise ValueError("unsupported fault evidence format")
        cases = fault_document.get("cases", {})
        if isinstance(cases, dict):
            fault_results = {
                name: cases.get(name, {}).get("pass")
                if isinstance(cases.get(name), dict) else None
                for name in REQUIRED_FAULTS
            }
    else:
        fault_results = {name: None for name in REQUIRED_FAULTS}
    fault_complete = all(value is True for value in fault_results.values())
    fault_failed = any(value is False for value in fault_results.values())
    result = "FAIL" if not clean_pass or fault_failed else (
        "PASS" if fault_complete else "PENDING"
    )

    return {
        "format": FORMAT,
        "generated_utc": utc_now(),
        "result": result,
        "run_path": str(bundle.path),
        "run_id": f"0x{bundle.run_id:016x}",
        "stream_id": f"0x{bundle.stream_id:08x}",
        "minimum_duration_seconds": minimum_duration,
        "clean_soak_pass": clean_pass,
        "fault_acceptance_complete": fault_complete,
        "checks": checks,
        "measurement": {
            "completed_shots": health.complete_shots,
            "incomplete_shots": health.incomplete_shots,
            "skipped_shot_ids": health.skipped_shot_ids,
            "duration_seconds": sender_duration,
            "shot_rate_hz": health.shot_rate_hz,
            "sample_payload_mbps": health.sample_payload_mbps,
            "udp_payload_mbps": health.udp_payload_mbps,
            "reference_words_checked": checked_words,
            "reference_mismatches": reference_mismatches,
        },
        "instrument_health": {
            "datagrams": health.datagrams,
            "duplicate_packets": health.duplicate_packets,
            "reordered_packets": health.reordered_packets,
            "missing_packets": health.missing_packets,
            "malformed_packets": health.malformed_packets,
            "conflicting_packets": health.conflicting_packets,
            "kernel_receive_drops": health.kernel_receive_drops,
            "token_queue_high_water": health.token_queue_high_water,
            "dma_ready_high_water": health.dma_ready_high_water,
            "dma_stall_cycles": health.dma_stall_cycles,
            "starvation_events": health.starvation_events,
            "missed_triggers": health.missed_triggers,
            "skipped_triggers": health.skipped_triggers,
            "dma_error_events": health.dma_error_events,
            "recorder_cpu_percent": health.recorder_cpu_percent,
            "sender_cpu_percent": health.sender_cpu_percent,
        },
        "recovery_tests": fault_results,
    }


def render_report(run_path: Path, summary: dict[str, Any], output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    bundle = RunBundle.open(run_path)
    if not bundle.complete_shots:
        raise ValueError("acceptance report needs at least one complete shot")
    analysis = analyzer.analyze_shot(bundle, bundle.complete_shots[-1])
    figure = Figure(figsize=(15.5, 11), dpi=100, facecolor="#f4f5f7")
    science, evidence = figure.subfigures(2, 1, height_ratios=(3.3, 1.0))
    analyzer.render_analysis(science, analysis)
    axis = evidence.subplots(1, 1)
    axis.set_axis_off()
    result = str(summary["result"])
    result_color = {"PASS": "#087f5b", "PENDING": "#9a6700", "FAIL": "#991b1b"}[result]
    measurement = summary["measurement"]
    health = summary["instrument_health"]
    faults = summary["recovery_tests"]
    axis.text(0.02, 0.85, f"DP-5D {result}", fontsize=20, weight="bold",
              color=result_color, transform=axis.transAxes)
    axis.text(
        0.02, 0.58,
        f"Run {summary['run_id']}  |  {measurement['duration_seconds']:.1f} s  |  "
        f"{measurement['completed_shots']:,} complete / "
        f"{measurement['incomplete_shots']} incomplete  |  "
        f"{measurement['shot_rate_hz']:.3f} shot/s  |  "
        f"{measurement['sample_payload_mbps']:.3f} Mb/s sample payload",
        fontsize=11, color="#111827", transform=axis.transAxes,
    )
    axis.text(
        0.02, 0.33,
        f"Integrity: {health['kernel_receive_drops']} kernel drops, "
        f"{health['missing_packets']} missing, "
        f"{health['missed_triggers']}/{health['skipped_triggers']} missed/skipped triggers, "
        f"{health['starvation_events']} pool backpressure events, "
        f"{measurement['reference_mismatches']} / "
        f"{measurement['reference_words_checked']:,} model mismatches  |  "
        f"Queue HWM token/DMA: {health['token_queue_high_water']}/"
        f"{health['dma_ready_high_water']} banks",
        fontsize=10.5, color="#374151", transform=axis.transAxes,
    )
    fault_text = "  |  ".join(
        f"{name.replace('_', ' ')}: "
        f"{'PENDING' if value is None else 'PASS' if value else 'FAIL'}"
        for name, value in faults.items()
    )
    axis.text(0.02, 0.09, fault_text, fontsize=10.5, color="#374151",
              transform=axis.transAxes)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=160, facecolor=figure.get_facecolor())


def load_faults(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"format": FAULT_FORMAT, "updated_utc": utc_now(), "cases": {}}
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("format") != FAULT_FORMAT or not isinstance(document.get("cases"), dict):
        raise ValueError("unsupported fault evidence format")
    return document


def published_payloads_valid(bundle: RunBundle) -> bool:
    return all(
        (binascii.crc32(bundle.read_samples(shot)) & 0xFFFFFFFF)
        == shot.payload_crc32
        for shot in bundle.complete_shots
    )


def accepted_bundle(path: Path) -> tuple[RunBundle, bool]:
    bundle = RunBundle.open(path)
    return bundle, (
        bundle.run_complete is True
        and bundle.sender_report is not None
        and bool(bundle.sender_report.get("complete"))
        and bundle.health().integrity_ok
        and published_payloads_valid(bundle)
    )


def assess_fault(args: argparse.Namespace) -> int:
    document = load_faults(args.faults)
    evidence: dict[str, Any]
    if args.case == "analyzer_restart":
        bundle, run_ok = accepted_bundle(args.run)
        sessions: set[str] = set()
        events = 0
        for line in args.session_log.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            if (item.get("format") == "qcrate-analyzer-session-v1"
                    and item.get("event") == "open_run"
                    and int(str(item.get("run_id", "0")), 0) == bundle.run_id):
                sessions.add(str(item.get("session_id")))
                events += 1
        passed = run_ok and len(sessions) >= 2
        evidence = {
            "run": str(bundle.path),
            "run_complete": run_ok,
            "session_log": str(args.session_log.resolve()),
            "open_events": events,
            "distinct_sessions": len(sessions),
        }
    else:
        disrupted, disrupted_ok = accepted_bundle(args.disrupted_run)
        recovery, recovery_ok = accepted_bundle(args.recovery_run)
        disrupted_payloads_ok = published_payloads_valid(disrupted)
        passed = (not disrupted_ok and disrupted.run_complete is False
                  and disrupted_payloads_ok
                  and recovery_ok and disrupted.run_id != recovery.run_id)
        evidence = {
            "disrupted_run": str(disrupted.path),
            "disrupted_manifest_complete": disrupted.run_complete,
            "disrupted_complete_shots": len(disrupted.complete_shots),
            "disrupted_incomplete_shots": len(disrupted.incomplete_shots),
            "disrupted_published_payloads_valid": disrupted_payloads_ok,
            "recovery_run": str(recovery.path),
            "recovery_complete": recovery_ok,
            "fresh_run_identity": disrupted.run_id != recovery.run_id,
        }
    document["updated_utc"] = utc_now()
    document["cases"][args.case] = {
        "pass": passed,
        "assessed_utc": utc_now(),
        "evidence": evidence,
    }
    atomic_json(args.faults, document)
    print(f"{'PASS' if passed else 'FAIL'} {args.case}: {args.faults}")
    return 0 if passed else 2


def ssh_command(target: str, remote: list[str] | str, *, tty: bool = False) -> list[str]:
    command = ["ssh"]
    if tty:
        command.append("-tt")
    command.extend([target, remote if isinstance(remote, str) else shlex.join(remote)])
    return command


def run_visible(command: list[str], log_path: Path) -> subprocess.CompletedProcess[bytes]:
    """Mirror a PTY-backed command to the terminal and an evidence log."""
    captured = bytearray()
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        try:
            assert process.stdout is not None
            while True:
                chunk = os.read(process.stdout.fileno(), 4096)
                if not chunk:
                    break
                captured.extend(chunk)
                log.write(chunk)
                log.flush()
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
            return subprocess.CompletedProcess(
                command, process.wait(), bytes(captured), None
            )
        except BaseException:
            process.terminate()
            process.wait(timeout=5)
            raise


def wait_for_path(path: Path, process: subprocess.Popen[str], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            raise RuntimeError("recorder exited before creating its run index")
        time.sleep(0.05)
    raise TimeoutError(f"timed out waiting for {path}")


def extract_remote_report(payload: bytes, begin: str, end: str) -> dict[str, Any]:
    """Extract one JSON report embedded in a PTY-backed SSH transcript."""
    transcript = payload.decode("utf-8", errors="replace").replace("\r\n", "\n")
    begin_at = transcript.rfind(begin)
    end_at = transcript.find(end, begin_at + len(begin))
    if begin_at < 0 or end_at < 0:
        raise RuntimeError("KV260 session did not return its sender report")
    encoded = transcript[begin_at + len(begin):end_at].strip()
    if not encoded:
        raise RuntimeError("KV260 session returned an empty sender report")
    document = json.loads(encoded)
    if not isinstance(document, dict) or document.get("format") != "qcrate-sender-report-v1":
        raise ValueError("unsupported sender report returned by KV260")
    return document


def preserve_log(source: Path, destination: Path) -> None:
    if source.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        source.unlink(missing_ok=True)


def print_verdict(summary: dict[str, Any], label: str) -> None:
    print(f"{summary['result']} {label}")
    failures = [item for item in summary["checks"] if not item["pass"]]
    for item in failures:
        actual = json.dumps(item["actual"], sort_keys=True)
        print(
            f"  FAIL {item['name']}: got {actual}; "
            f"required {item['requirement']}"
        )
    pending_faults = [
        name for name, value in summary["recovery_tests"].items()
        if value is None
    ]
    if not failures and pending_faults:
        print("  PENDING fault acceptance: " + ", ".join(pending_faults))


def run_soak(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    if output.exists():
        raise ValueError(f"output already exists: {output}")
    recorder = args.recorder.resolve()
    if not recorder.exists():
        subprocess.run([sys.executable, str(ROOT / "host/data_plane/build_recorder.py")],
                       cwd=ROOT, check=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    recorder_log = output.parent / f".{output.name}-recorder.log"
    sender_log = output.parent / f".{output.name}-sender.log"
    with recorder_log.open("w", encoding="utf-8") as log:
        recorder_process = subprocess.Popen([
            str(recorder), "--bind", args.bind, "--port", str(args.port),
            "--source", args.source, "--output", str(output),
            "--wait-timeout-ms", str(args.startup_timeout_seconds * 1000),
            "--idle-timeout-ms", "5000",
        ], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, text=True)
    wait_for_path(output / "shots.qidx", recorder_process, 5.0)

    if args.gui:
        subprocess.Popen([
            sys.executable, str(ROOT / "host/analyzer/qcrate_analyzer.py"),
            str(output), "--session-log", str(output / "analyzer-sessions.jsonl"),
        ], cwd=ROOT)

    report_token = uuid.uuid4().hex
    remote_report = f"/tmp/qcrate-dp5d-{report_token}.json"
    report_begin = f"__QCRATE_REPORT_{report_token}_BEGIN__"
    report_end = f"__QCRATE_REPORT_{report_token}_END__"
    streamer = [
        "qcrate-streamer",
        "--destination", args.destination,
        "--port", str(args.port),
        "--duration-seconds", str(args.duration_seconds),
        "--banks", str(args.banks),
        "--rate-mbps", str(args.rate_mbps),
        "--report-json", remote_report,
    ]
    if args.rpmsg_device:
        streamer.extend(["--rpmsg-device", args.rpmsg_device])
    commands = []
    if not args.skip_sequence_load:
        commands.append(shlex.join(["qcrate-sequence", "load", args.sequence]))
    commands.append(shlex.join(streamer))
    workload = " && ".join(commands)
    remote_script = (
        f"{workload}; status=$?; "
        f"printf '\\n{report_begin}\\n'; "
        f"if [ -r {shlex.quote(remote_report)} ]; then "
        f"cat {shlex.quote(remote_report)}; fi; "
        f"printf '\\n{report_end}\\n'; "
        f"rm -f {shlex.quote(remote_report)}; "
        "exit $status"
    )
    remote_command = shlex.join([
        "sudo", "-p", "[KV260 sudo] password: ", "--", "sh", "-c",
        remote_script,
    ])
    sender_result: subprocess.CompletedProcess[bytes] | None = None
    recorder_status = -1
    try:
        sender_result = run_visible(
            ssh_command(args.board, remote_command, tty=True), sender_log
        )
        sender_document = extract_remote_report(
            sender_result.stdout, report_begin, report_end
        )
        sender_name = (
            "sender.json" if sender_result.returncode == 0
            else "sender-failed.json"
        )
        atomic_json(output / sender_name, sender_document)
    finally:
        if recorder_process.poll() is None and (
            sender_result is None or sender_result.returncode != 0
        ):
            recorder_process.terminate()
        try:
            recorder_status = recorder_process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            recorder_process.terminate()
            recorder_status = recorder_process.wait(timeout=5)
        preserve_log(recorder_log, output / "recorder.log")
        preserve_log(sender_log, output / "sender.log")

    assert sender_result is not None
    if sender_result.returncode:
        raise RuntimeError(
            f"KV260 sender exited with status {sender_result.returncode}; "
            f"see {output / 'sender.log'}"
        )
    if recorder_status:
        raise RuntimeError(
            f"recorder exited with status {recorder_status}; "
            f"see {output / 'recorder.log'}"
        )
    summary = evaluate_run(
        output, minimum_duration=float(args.duration_seconds),
        faults_path=args.faults,
    )
    atomic_json(output / "dp5d-acceptance.json", summary)
    render_report(output, summary, output / "dp5d-acceptance.png")
    print_verdict(summary, f"clean DP-5D run: {output}")
    return 0 if summary["clean_soak_pass"] else 2


def evaluate_command(args: argparse.Namespace) -> int:
    summary = evaluate_run(args.run, minimum_duration=args.minimum_duration,
                           faults_path=args.faults)
    output = args.output or args.run / "dp5d-acceptance.json"
    image = args.image or args.run / "dp5d-acceptance.png"
    atomic_json(output, summary)
    render_report(args.run, summary, image)
    print_verdict(summary, f"wrote {output} and {image}")
    return 0 if summary["result"] in ("PASS", "PENDING") else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run recorder and KV260 soak")
    run_parser.add_argument("--board", required=True, help="SSH target, e.g. petalinux@192.168.1.93")
    run_parser.add_argument("--destination", required=True, help="host address visible to KV260")
    run_parser.add_argument("--bind", default="0.0.0.0")
    run_parser.add_argument("--source", required=True, help="accepted KV260 source address")
    run_parser.add_argument("--port", type=int, default=47000)
    run_parser.add_argument("--output", type=Path, required=True)
    run_parser.add_argument("--duration-seconds", type=int, default=300)
    run_parser.add_argument(
        "--startup-timeout-seconds", type=int, default=300,
        help="maximum wait for SSH/sudo authentication before the first datagram",
    )
    run_parser.add_argument("--banks", type=int, default=4)
    run_parser.add_argument("--rate-mbps", type=int, default=420)
    run_parser.add_argument("--sequence", default="/home/petalinux/qcrate/two_channel_demo.qseq")
    run_parser.add_argument("--rpmsg-device")
    run_parser.add_argument("--recorder", type=Path,
                            default=ROOT / "build/host/qcrate-recorder")
    run_parser.add_argument("--faults", type=Path)
    run_parser.add_argument("--gui", action=argparse.BooleanOptionalAction,
                            default=True)
    run_parser.add_argument("--skip-sequence-load", action="store_true")
    run_parser.set_defaults(function=run_soak)

    evaluate_parser = subparsers.add_parser("evaluate", help="evaluate existing artifacts")
    evaluate_parser.add_argument("run", type=Path)
    evaluate_parser.add_argument("--minimum-duration", type=float, default=300.0)
    evaluate_parser.add_argument("--faults", type=Path)
    evaluate_parser.add_argument("--output", type=Path)
    evaluate_parser.add_argument("--image", type=Path)
    evaluate_parser.set_defaults(function=evaluate_command)

    fault_parser = subparsers.add_parser("assess-fault", help="record evidence-based recovery result")
    fault_parser.add_argument("case", choices=REQUIRED_FAULTS)
    fault_parser.add_argument("--faults", type=Path, required=True)
    fault_parser.add_argument("--run", type=Path)
    fault_parser.add_argument("--session-log", type=Path)
    fault_parser.add_argument("--disrupted-run", type=Path)
    fault_parser.add_argument("--recovery-run", type=Path)
    fault_parser.set_defaults(function=assess_fault)
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.command == "run":
        if (not 1 <= args.port <= 65535 or args.duration_seconds < 1
                or args.startup_timeout_seconds < 1):
            raise ValueError(
                "port, duration, and startup timeout must be positive and in range"
            )
    if args.command == "evaluate" and args.minimum_duration < 0:
        raise ValueError("minimum duration cannot be negative")
    if args.command == "assess-fault":
        if args.case == "analyzer_restart":
            if args.run is None or args.session_log is None:
                raise ValueError("analyzer_restart requires --run and --session-log")
        elif args.disrupted_run is None or args.recovery_run is None:
            raise ValueError(f"{args.case} requires --disrupted-run and --recovery-run")


def main() -> int:
    args = parse_args()
    try:
        validate_args(args)
        return int(args.function(args))
    except (OSError, ValueError, RuntimeError, TimeoutError,
            subprocess.SubprocessError) as exc:
        raise SystemExit(f"error: {exc}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
