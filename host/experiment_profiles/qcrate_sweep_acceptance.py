#!/usr/bin/env python3
"""Run DP-6E configuration safety, recovery, and stale-identity acceptance."""
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import qcrate_experiment as experiment  # noqa: E402
import qcrate_profile as profile_contract  # noqa: E402
import qcrate_sweep as sweep  # noqa: E402


FAULT_FORMAT = "qcrate-dp6e-faults-v1"
ACCEPTANCE_FORMAT = "qcrate-dp6e-acceptance-v1"
REQUIRED_FAULT_CASES = {
    "invalid_profile_rejection",
    "commit_while_armed",
    "configuration_tool_restart",
    "interrupted_run_recovery",
    "stale_id_detection",
}


def _stage_command(resolved: dict[str, Any]) -> list[str]:
    command = profile_contract.configuration_command(resolved, "qcrate-control")
    command[1] = "config-stage"
    return command


def target_fault_script(
    active: dict[str, Any], alternate: dict[str, Any], sequence: str
) -> str:
    """Create one fail-closed target session for all R5 semantic checks."""
    active_apply = profile_contract.configuration_command(active, "qcrate-control")
    invalid = _stage_command(active)
    invalid[2] = "0x0000000000000001"
    invalid[5] = "0x00008000"
    alternate_stage = _stage_command(alternate)
    expected_id = alternate["identity"]["dsp_config_id"]
    commands = {
        "active_apply": shlex.join(active_apply),
        "invalid_stage": shlex.join(invalid),
        "alternate_stage": shlex.join(alternate_stage),
        "sequence_load": shlex.join(["qcrate-sequence", "load", sequence]),
    }
    active_id = active["identity"]["dsp_config_id"]
    return f"""
set -u
failed=0

{commands['active_apply']} || failed=1
{commands['invalid_stage']} || failed=1
invalid_output=$(qcrate-control config-validate 2>&1)
invalid_status=$?
printf '%s\n' "$invalid_output"
invalid_active=$(qcrate-control config-status 2>&1)
printf '%s\n' "$invalid_active"
if [ "$invalid_status" -ne 0 ] && \
   printf '%s\n' "$invalid_output" | grep -q 'R5 reason        : 3 ' && \
   printf '%s\n' "$invalid_active" | grep -qi 'active config ID : {active_id}'; then
    echo __QCRATE_INVALID_PROFILE_PASS__
else
    echo __QCRATE_INVALID_PROFILE_FAIL__
    failed=1
fi
qcrate-control config-recover || failed=1

{commands['alternate_stage']} || failed=1
qcrate-control config-validate || failed=1
{commands['sequence_load']} || failed=1
qcrate-sequence arm || failed=1
armed_output=$(qcrate-control config-commit 2>&1)
armed_status=$?
printf '%s\n' "$armed_output"
armed_active=$(qcrate-control config-status 2>&1)
printf '%s\n' "$armed_active"
if [ "$armed_status" -ne 0 ] && \
   printf '%s\n' "$armed_output" | grep -q 'R5 reason        : 10 ' && \
   printf '%s\n' "$armed_active" | grep -qi 'active config ID : {active_id}'; then
    echo __QCRATE_ARMED_COMMIT_PASS__
else
    echo __QCRATE_ARMED_COMMIT_FAIL__
    failed=1
fi
qcrate-sequence abort || failed=1
qcrate-control config-recover || failed=1

{commands['alternate_stage']} || failed=1
qcrate-control config-validate || failed=1
qcrate-control config-commit || failed=1
restart_output=$(qcrate-control config-status 2>&1)
restart_status=$?
printf '%s\n' "$restart_output"
if [ "$restart_status" -eq 0 ] && \
   printf '%s\n' "$restart_output" | grep -qi 'active config ID : {expected_id}'; then
    echo __QCRATE_TOOL_RESTART_PASS__
else
    echo __QCRATE_TOOL_RESTART_FAIL__
    failed=1
fi
exit "$failed"
""".strip()


def run_target_faults(
    root: Path,
    args: argparse.Namespace,
    active: dict[str, Any],
    alternate: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    script = target_fault_script(active, alternate, args.sequence)
    log = root / "fault-evidence" / "target-semantics.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    result = experiment.run_visible(experiment.ssh_target_command(args, script), log)
    transcript = result.stdout.decode("utf-8", errors="replace")
    cases = {
        "invalid_profile_rejection": {
            "pass": "__QCRATE_INVALID_PROFILE_PASS__" in transcript,
            "requirement": (
                "R5 rejects an invalid Q1.15 amplitude and preserves the active profile"
            ),
        },
        "commit_while_armed": {
            "pass": "__QCRATE_ARMED_COMMIT_PASS__" in transcript,
            "requirement": (
                "R5 rejects configuration commit while the sequence engine is armed"
            ),
        },
        "configuration_tool_restart": {
            "pass": "__QCRATE_TOOL_RESTART_PASS__" in transcript,
            "requirement": (
                "stage, validate, and commit survive separate qcrate-control processes"
            ),
        },
    }
    return cases, result.returncode


def interrupt_recorder(root: Path, args: argparse.Namespace) -> Path:
    """Create and preserve one genuinely interrupted recorder artifact."""
    recorder = args.recorder.resolve()
    if not recorder.exists():
        subprocess.run(
            [sys.executable, str(sweep.ROOT / "host/data_plane/build_recorder.py")],
            cwd=sweep.ROOT, check=True,
        )
    run = root / "fault-evidence" / "recovery-run"
    if run.exists():
        sweep._preserve_interrupted(run)
    log_path = root / "fault-evidence" / "interrupted-recorder.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen([
            str(recorder), "--bind", args.bind, "--port", str(args.port),
            "--source", args.source, "--output", str(run),
            "--wait-timeout-ms", "10000", "--idle-timeout-ms", "5000",
        ], cwd=sweep.ROOT, stdout=log, stderr=subprocess.STDOUT, text=True)
    try:
        experiment.wait_for_path(run / "shots.qidx", process, 5.0)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    return sweep._preserve_interrupted(run)


def run_recovery_faults(
    root: Path,
    args: argparse.Namespace,
    active_path: Path,
    stale_path: Path,
) -> dict[str, Any]:
    preserved = interrupt_recorder(root, args)
    recovery_run = root / "fault-evidence" / "recovery-run"
    recovery_args = argparse.Namespace(
        profile=active_path,
        board=args.board,
        destination=args.destination,
        source=args.source,
        output=recovery_run,
        bind=args.bind,
        port=args.port,
        shots=1,
        banks=args.banks,
        rate_mbps=args.rate_mbps,
        sequence=args.sequence,
        recorder=args.recorder,
        startup_timeout_seconds=args.startup_timeout_seconds,
        snapshot=recovery_run / "measurement.png",
        gui=False,
        unattended=args.unattended,
    )
    experiment.run_experiment(recovery_args)
    active = profile_contract.load_resolved_profile(active_path)
    stale = profile_contract.load_resolved_profile(stale_path)
    experiment.verify_run(recovery_run, active)
    stale_rejected = False
    try:
        experiment.verify_run(recovery_run, stale)
    except RuntimeError:
        stale_rejected = True
    return {
        "interrupted_run_recovery": {
            "pass": preserved.exists() and recovery_run.exists(),
            "preserved_run": str(preserved.relative_to(root)),
            "fresh_run": str(recovery_run.relative_to(root)),
            "requirement": (
                "interrupted recorder evidence is retained and recovery starts a fresh complete run"
            ),
        },
        "stale_id_detection": {
            "pass": stale_rejected,
            "active_config_id": active["identity"]["dsp_config_id"],
            "stale_config_id": stale["identity"]["dsp_config_id"],
            "requirement": (
                "a complete recording cannot be verified with a stale resolved profile"
            ),
        },
    }


def finalize_acceptance(root: Path, faults: dict[str, Any]) -> dict[str, Any]:
    sweep_path = root / "dp6e-sweep.json"
    sweep_report = (
        sweep.load_json(sweep_path)
        if sweep_path.exists()
        else sweep.evaluate_sweep(root)
    )
    if sweep_report.get("format") != sweep.REPORT_FORMAT:
        raise sweep.SweepError("unsupported DP-6E sweep report")
    cases = faults.get("cases")
    if faults.get("format") != FAULT_FORMAT or not isinstance(cases, dict):
        raise sweep.SweepError("unsupported DP-6E fault report")
    if set(cases) != REQUIRED_FAULT_CASES:
        raise sweep.SweepError("DP-6E fault report does not contain the required cases")
    passed = (
        sweep_report["result"] == "PASS"
        and faults.get("target_command_status") == 0
        and all(
            isinstance(case, dict) and case.get("pass") is True
            for case in cases.values()
        )
    )
    acceptance = {
        **sweep_report,
        "format": ACCEPTANCE_FORMAT,
        "generated_utc": sweep.utc_now(),
        "result": "PASS" if passed else "FAIL",
        "faults": faults,
    }
    sweep.atomic_json(root / "dp6e-acceptance.json", acceptance)
    sweep.render_report(acceptance, root / "dp6e-acceptance.png")
    return acceptance


def run_acceptance(args: argparse.Namespace) -> int:
    root = args.sweep.resolve()
    if args.unattended:
        experiment.verify_unattended_access(args.board)
    plan = sweep.load_plan(root)
    active_point = plan["points"][-1]
    alternate_point = plan["points"][0]
    active_path = sweep._sweep_path(
        root, active_point["resolved_profile"], "active resolved-profile path"
    )
    alternate_path = sweep._sweep_path(
        root, alternate_point["resolved_profile"], "alternate resolved-profile path"
    )
    active = profile_contract.load_resolved_profile(active_path)
    alternate = profile_contract.load_resolved_profile(alternate_path)
    target_cases, target_status = run_target_faults(root, args, active, alternate)
    recovery_cases = run_recovery_faults(
        root, args, alternate_path, active_path
    )
    faults = {
        "format": FAULT_FORMAT,
        "generated_utc": sweep.utc_now(),
        "target_command_status": target_status,
        "cases": {**target_cases, **recovery_cases},
    }
    sweep.atomic_json(root / "dp6e-faults.json", faults)
    acceptance = finalize_acceptance(root, faults)
    print(
        f"{acceptance['result']} DP-6E acceptance: "
        f"{sum(case['pass'] for case in faults['cases'].values())}/"
        f"{len(faults['cases'])} safety/recovery tests passed"
    )
    print(f"summary : {root / 'dp6e-acceptance.json'}")
    print(f"report  : {root / 'dp6e-acceptance.png'}")
    return 0 if acceptance["result"] == "PASS" else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sweep", required=True, type=Path)
    parser.add_argument("--board", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--bind")
    parser.add_argument("--port", type=int, default=47000)
    parser.add_argument("--banks", type=int, default=4)
    parser.add_argument("--rate-mbps", type=int, default=420)
    parser.add_argument("--sequence", default=experiment.DEFAULT_SEQUENCE)
    parser.add_argument("--recorder", type=Path, default=experiment.DEFAULT_RECORDER)
    parser.add_argument("--startup-timeout-seconds", type=int, default=30)
    parser.add_argument(
        "--interactive-auth", action="store_true",
        help="use legacy SSH and sudo password prompts instead of unattended access",
    )
    args = parser.parse_args()
    args.bind = args.bind or args.destination
    args.unattended = not args.interactive_auth
    if not 1 <= args.port <= 65535 or not 2 <= args.banks <= 64:
        parser.error("port or banks are outside their supported range")
    if args.rate_mbps < 0 or args.startup_timeout_seconds < 1:
        parser.error("rate and startup timeout must not be negative")
    return args


def main() -> int:
    try:
        return run_acceptance(parse_args())
    except (
        OSError,
        sweep.SweepError,
        ValueError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
