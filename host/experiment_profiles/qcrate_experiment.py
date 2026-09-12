#!/usr/bin/env python3
"""Run, record, verify, and render one resolved Q-Crate experiment."""
from __future__ import annotations

import argparse
import binascii
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import numpy as np


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ANALYZER_DIR = ROOT / "host" / "analyzer"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ANALYZER_DIR))

import qcrate_analyzer as analyzer  # noqa: E402
import qcrate_profile as profile_contract  # noqa: E402
from qcrate_run import RunBundle  # noqa: E402


DEFAULT_RECORDER = ROOT / "build" / "host" / "qcrate-recorder"
DEFAULT_SEQUENCE = "/home/petalinux/qcrate/two_channel_demo.qseq"
SSH_BATCH_OPTIONS = [
    "-o", "BatchMode=yes",
    "-o", "ConnectionAttempts=1",
    "-o", "ConnectTimeout=10",
    "-o", "ControlMaster=auto",
    "-o", "ControlPersist=60",
    "-o", "ControlPath=~/.ssh/qcrate-%C",
]


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_stream:
        fd, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.", dir=destination.parent
        )
        try:
            with os.fdopen(fd, "wb") as output_stream:
                shutil.copyfileobj(input_stream, output_stream)
                output_stream.flush()
                os.fsync(output_stream.fileno())
            os.replace(temporary, destination)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise


def atomic_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
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


def run_visible(command: list[str], log_path: Path) -> subprocess.CompletedProcess[bytes]:
    captured = bytearray()
    with log_path.open("wb") as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            assert process.stdout is not None
            while chunk := os.read(process.stdout.fileno(), 4096):
                captured.extend(chunk)
                log.write(chunk)
                log.flush()
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
        except BaseException:
            process.terminate()
            process.wait(timeout=5)
            raise
    return subprocess.CompletedProcess(command, process.wait(), bytes(captured), None)


def ssh_target_command(args: argparse.Namespace, script: str) -> list[str]:
    """Build either a direct unattended or legacy interactive target command."""
    if getattr(args, "unattended", False):
        remote = shlex.join(["sh", "-c", script])
        return ["ssh", *SSH_BATCH_OPTIONS, "-T", args.board, remote]
    remote = shlex.join([
        "sudo", "-p", "[KV260 sudo] password: ", "--", "sh", "-c", script,
    ])
    return ["ssh", "-tt", args.board, remote]


def verify_unattended_access(board: str) -> None:
    """Fail before acquisition unless SSH keys and Q-Crate device ACLs work."""
    probe = (
        "test -r /dev/qcrate-dma && test -w /dev/qcrate-dma && "
        "qcrate-control config-status >/dev/null"
    )
    result = subprocess.run(
        ["ssh", *SSH_BATCH_OPTIONS, "-T", board, probe],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
    )
    if result.returncode:
        detail = result.stderr.strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        raise RuntimeError(
            "unattended KV260 access failed; install the host SSH key and deploy "
            "the qcrate-access device-group policy, or use --interactive-auth"
            f"{suffix}"
        )


def extract_report(payload: bytes, begin: str, end: str) -> dict[str, Any]:
    transcript = payload.decode("utf-8", errors="replace").replace("\r\n", "\n")
    begin_at = transcript.rfind(begin)
    end_at = transcript.find(end, begin_at + len(begin))
    if begin_at < 0 or end_at < 0:
        raise RuntimeError("KV260 session did not return its sender report")
    document = json.loads(transcript[begin_at + len(begin):end_at].strip())
    if not isinstance(document, dict) or document.get("format") != "qcrate-sender-report-v1":
        raise ValueError("unsupported sender report returned by KV260")
    return document


def remote_workload(
    resolved: dict[str, Any], args: argparse.Namespace, report_path: str,
    begin: str, end: str,
) -> str:
    apply_command = profile_contract.configuration_command(resolved, "qcrate-control")
    acquisition = resolved["acquisition"]
    streamer = [
        "qcrate-streamer", "--destination", args.destination,
        "--port", str(args.port), "--triggered-shots", str(args.shots),
        "--banks", str(args.banks), "--rate-mbps", str(args.rate_mbps),
        "--words", str(acquisition["frame_length_words"]),
        "--frames", str(acquisition["frame_count"]),
        "--report-json", report_path,
    ]
    operations = [
        shlex.join(["sh", "-c", (
            f"printf '%s  %s\\n' {shlex.quote(resolved['sequence']['image_sha256'])} "
            f"{shlex.quote(args.sequence)} | sha256sum -c -"
        )]),
        shlex.join(apply_command),
        shlex.join(["qcrate-sequence", "load", args.sequence]),
        shlex.join(streamer),
    ]
    workload = " && ".join(operations)
    return (
        f"{workload}; status=$?; "
        f"printf '\\n{begin}\\n'; "
        f"if [ -r {shlex.quote(report_path)} ]; then cat {shlex.quote(report_path)}; fi; "
        f"printf '\\n{end}\\n'; rm -f {shlex.quote(report_path)}; exit $status"
    )


def verify_run(run_path: Path, resolved: dict[str, Any]) -> tuple[int, int, float]:
    bundle = RunBundle.open(run_path)
    expected_id = int(resolved["identity"]["dsp_config_id"], 0)
    expected_center_hz = int(
        Decimal(resolved["dsp"]["realized"]["lo_frequency_hz"])
        .to_integral_value(rounding=ROUND_HALF_UP)
    )
    if bundle.run_complete is not True or not bundle.health().integrity_ok:
        raise RuntimeError("recorded run is incomplete or reports an integrity failure")
    if not bundle.complete_shots:
        raise RuntimeError("recorded run contains no complete shots")
    stream = bundle.manifest.get("stream") if bundle.manifest else None
    if not isinstance(stream, dict) or int(str(stream.get("config_id", "0")), 0) != expected_id:
        raise RuntimeError("run manifest configuration ID differs from the profile")
    if int(stream.get("center_frequency_hz", -1)) != expected_center_hz:
        raise RuntimeError("run manifest center frequency differs from the profile")
    sender_stream = bundle.sender_report.get("stream") if bundle.sender_report else None
    if (not isinstance(sender_stream, dict) or
            int(str(sender_stream.get("config_id", "0")), 0) != expected_id):
        raise RuntimeError("sender report configuration ID differs from the profile")
    if int(sender_stream.get("center_frequency_hz", -1)) != expected_center_hz:
        raise RuntimeError("sender report center frequency differs from the profile")

    expected_by_count: dict[int, np.ndarray] = {}
    checked = 0
    for shot in bundle.complete_shots:
        if shot.config_id != expected_id:
            raise RuntimeError(
                f"shot {shot.shot_id} has configuration 0x{shot.config_id:016x}; "
                f"expected 0x{expected_id:016x}"
            )
        payload = bundle.read_samples(shot)
        if binascii.crc32(payload) & 0xFFFF_FFFF != shot.payload_crc32:
            raise RuntimeError(f"shot {shot.shot_id} payload CRC does not match QIDX")
        words = np.frombuffer(payload, dtype="<u4")
        expected = expected_by_count.get(len(words))
        if expected is None:
            expected = analyzer.expected_words(
                str((run_path / analyzer.RUN_PROFILE_NAME).resolve()), len(words)
            )
            expected_by_count[len(words)] = expected
        mismatches = int(np.count_nonzero(words != expected))
        if mismatches:
            raise RuntimeError(
                f"shot {shot.shot_id} has {mismatches} bit-exact model mismatches"
            )
        checked += len(words)
    analysis = analyzer.analyze_shot(bundle, bundle.complete_shots[0])
    return len(bundle.complete_shots), checked, analysis.dominant_frequency_hz


def run_experiment(args: argparse.Namespace) -> int:
    source_profile = args.profile.resolve()
    resolved = profile_contract.load_resolved_profile(source_profile)
    output = args.output.resolve()
    if output.exists():
        raise ValueError(f"output already exists: {output}")
    recorder = args.recorder.resolve()
    if not recorder.exists():
        subprocess.run(
            [sys.executable, str(ROOT / "host/data_plane/build_recorder.py")],
            cwd=ROOT, check=True,
        )
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

    sender_result: subprocess.CompletedProcess[bytes] | None = None
    try:
        wait_for_path(output / "shots.qidx", recorder_process, 5.0)
        atomic_copy(source_profile, output / analyzer.RUN_PROFILE_NAME)

        token = uuid.uuid4().hex
        remote_report = f"/tmp/qcrate-dp6d-{token}.json"
        begin = f"__QCRATE_REPORT_{token}_BEGIN__"
        end = f"__QCRATE_REPORT_{token}_END__"
        script = remote_workload(resolved, args, remote_report, begin, end)
        sender_result = run_visible(
            ssh_target_command(args, script), sender_log
        )
        report = extract_report(sender_result.stdout, begin, end)
        atomic_json(output / "sender.json", report)
        if sender_result.returncode:
            raise RuntimeError(
                f"KV260 experiment exited with status {sender_result.returncode}"
            )
        try:
            recorder_status = recorder_process.wait(timeout=15)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("recorder did not close after end-of-stream") from error
        if recorder_status:
            raise RuntimeError(f"recorder exited with status {recorder_status}")

        shots, words, dominant_hz = verify_run(output, resolved)
        snapshot = args.snapshot.resolve() if args.snapshot else output / "measurement.png"
        analyzer.write_snapshot(
            output, snapshot, shot_id=None, fallback_rate=analyzer.DEFAULT_SAMPLE_RATE_HZ
        )
        print(
            f"PASS DP-6D experiment: {shots} shot(s), {words} IQ words, "
            f"config {resolved['identity']['dsp_config_id']}"
        )
        print(f"dominant baseband : {dominant_hz / 1.0e6:.6f} MHz")
        print(f"run              : {output}")
        print(f"measurement      : {snapshot}")
        if args.gui:
            subprocess.Popen([
                sys.executable, str(ANALYZER_DIR / "qcrate_analyzer.py"), str(output)
            ], cwd=ROOT)
        return 0
    finally:
        if recorder_process.poll() is None:
            recorder_process.terminate()
            try:
                recorder_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                recorder_process.kill()
                recorder_process.wait()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--board", required=True, help="SSH target, for example petalinux@192.168.1.93")
    parser.add_argument("--destination", required=True, help="host address seen by KV260")
    parser.add_argument("--source", required=True, help="KV260 source address accepted by recorder")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--bind", help="host bind address; defaults to --destination")
    parser.add_argument("--port", type=int, default=47000)
    parser.add_argument("--shots", type=int, default=10)
    parser.add_argument("--banks", type=int, default=4)
    parser.add_argument("--rate-mbps", type=int, default=420)
    parser.add_argument("--sequence", default=DEFAULT_SEQUENCE)
    parser.add_argument("--recorder", type=Path, default=DEFAULT_RECORDER)
    parser.add_argument("--startup-timeout-seconds", type=int, default=30)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--gui", action="store_true", help="open the accepted run after verification")
    parser.add_argument(
        "--unattended", action="store_true",
        help="require SSH-key and direct Q-Crate device access; never prompt",
    )
    args = parser.parse_args()
    args.bind = args.bind or args.destination
    if not 1 <= args.port <= 65535 or args.shots < 1 or not 2 <= args.banks <= 64:
        parser.error("port, shots, or banks are outside their supported range")
    if args.rate_mbps < 0 or args.startup_timeout_seconds < 1:
        parser.error("rate and startup timeout must not be negative")
    return args


def main() -> int:
    try:
        return run_experiment(parse_args())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
