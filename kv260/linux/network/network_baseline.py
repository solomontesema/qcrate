#!/usr/bin/env python3
"""Collect a compact, reproducible Q-Crate network-state snapshot.

Run this on both the development host and the KV260. The script intentionally
uses standard Linux tools rather than changing interface configuration. iperf3
measurements remain separate so their JSON results can be compared directly.
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence


def run(command: Sequence[str]) -> tuple[int, str]:
    """Run one read-only diagnostic command and return merged text output."""
    executable = shutil.which(command[0])
    if executable is None:
        return 127, f"SKIP: {command[0]} is not installed\n"

    result = subprocess.run(
        [executable, *command[1:]],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return result.returncode, result.stdout


def section(title: str, command: Sequence[str]) -> str:
    """Format a command and its result as one stable report section."""
    return_code, output = run(command)
    rendered = shlex.join(command)
    return (
        f"\n## {title}\n"
        f"$ {rendered}\n"
        f"exit_status={return_code}\n"
        f"{output.rstrip()}\n"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect Linux network state and an optional peer ping.",
    )
    parser.add_argument(
        "--interface",
        required=True,
        help="Interface to inspect, for example enp3s0f1 or end0.",
    )
    parser.add_argument(
        "--peer",
        help="Optional peer IPv4 address to ping ten times.",
    )
    parser.add_argument(
        "--label",
        default="unnamed-node",
        help="Report node label, for example host or kv260.",
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        help="Write the report to this path instead of standard output.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    report = [
        "# Q-Crate network baseline\n",
        f"label={args.label}\n",
        f"captured_utc={timestamp}\n",
        f"interface={args.interface}\n",
        f"peer={args.peer or 'not-requested'}\n",
    ]

    commands: list[tuple[str, list[str]]] = [
        ("Kernel", ["uname", "-a"]),
        ("Interface links", ["ip", "-br", "link"]),
        ("Interface addresses", ["ip", "-br", "address"]),
        ("IPv4 routes", ["ip", "-4", "route"]),
        ("Peer route", ["ip", "route", "get", args.peer])
        if args.peer
        else ("Selected interface", ["ip", "address", "show", "dev", args.interface]),
        ("Ethernet link", ["ethtool", args.interface]),
        (
            "Socket limits",
            [
                "sysctl",
                "net.core.rmem_default",
                "net.core.rmem_max",
                "net.core.wmem_default",
                "net.core.wmem_max",
                "net.ipv4.udp_rmem_min",
                "net.ipv4.udp_wmem_min",
            ],
        ),
    ]

    if args.peer:
        commands.append(("Peer ping", ["ping", "-c", "10", "-W", "1", args.peer]))

    for title, command in commands:
        report.append(section(title, command))

    text = "".join(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        sys.stdout.write(text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
