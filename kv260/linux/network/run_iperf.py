#!/usr/bin/env python3
"""Run the reproducible Q-Crate bidirectional iperf3 acceptance sweep."""
from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import iperf_summary


@dataclass(frozen=True)
class Measurement:
    """One iperf invocation and its stable result filename."""

    name: str
    arguments: tuple[str, ...]


def measurements(
    server: str,
    duration: int,
    omit: int,
    payload_bytes: int,
    udp_rates: list[str],
) -> list[Measurement]:
    """Construct the complete forward/reverse TCP and UDP sweep."""
    common = ("-c", server, "-t", str(duration), "-O", str(omit))
    tests = [
        Measurement("tcp-host-to-kv260", common),
        Measurement("tcp-kv260-to-host", (*common, "-R")),
    ]
    for rate in udp_rates:
        udp = (*common, "-u", "-b", rate, "-l", str(payload_bytes))
        tests.append(Measurement(f"udp-host-to-kv260-{rate}", udp))
        tests.append(Measurement(f"udp-kv260-to-host-{rate}", (*udp, "-R")))
    return tests


def run_measurement(
    executable: str,
    measurement: Measurement,
    output_dir: Path,
    timeout_seconds: int,
) -> Path:
    """Run one test and publish its validated JSON result atomically."""
    destination = output_dir / f"{measurement.name}.json"
    command = [
        executable,
        *measurement.arguments,
        "--get-server-output",
        "--json",
    ]
    print(f"+ {shlex.join(command)}")
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=output_dir, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
        try:
            subprocess.run(
                command,
                check=True,
                stdout=temporary,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_seconds,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            temporary_path.unlink(missing_ok=True)
            detail = getattr(exc, "stderr", None) or str(exc)
            raise RuntimeError(f"{measurement.name} failed: {detail.strip()}") from exc

    try:
        iperf_summary.parse_iperf(temporary_path)
        temporary_path.replace(destination)
    except (OSError, ValueError):
        temporary_path.unlink(missing_ok=True)
        raise
    return destination


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True, help="KV260 IPv4 or DNS address")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--omit", type=int, default=3)
    parser.add_argument("--payload-bytes", type=int, default=1400)
    parser.add_argument(
        "--udp-rates",
        nargs="+",
        default=["100M", "500M", "800M", "900M"],
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print commands without running iperf3"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.duration <= 0 or args.omit < 0:
        raise SystemExit("error: duration must be positive and omit must be nonnegative")
    if not 256 <= args.payload_bytes <= 1400:
        raise SystemExit("error: payload bytes must be between 256 and 1400")

    executable = shutil.which("iperf3")
    if executable is None:
        raise SystemExit("error: iperf3 is not installed")
    tests = measurements(
        args.server,
        args.duration,
        args.omit,
        args.payload_bytes,
        args.udp_rates,
    )
    if args.dry_run:
        for test in tests:
            print(
                "+ "
                + shlex.join(
                    [executable, *test.arguments, "--get-server-output", "--json"]
                )
            )
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stale_results = sorted(args.output_dir.glob("*.json"))
    summary_path = args.output_dir / "iperf-summary.md"
    if stale_results or summary_path.exists():
        names = ", ".join(
            path.name
            for path in [*stale_results, summary_path]
            if path.exists()
        )
        raise SystemExit(f"error: output directory contains iperf results: {names}")
    timeout_seconds = args.duration + args.omit + 20
    paths: list[Path] = []
    try:
        for test in tests:
            paths.append(
                run_measurement(executable, test, args.output_dir, timeout_seconds)
            )
    except (OSError, ValueError, RuntimeError) as exc:
        raise SystemExit(f"error: {exc}") from exc

    summary = iperf_summary.render(
        [iperf_summary.parse_iperf(path) for path in paths], paths
    )
    summary_path.write_text(summary, encoding="utf-8")
    print(f"Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
