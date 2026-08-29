#!/usr/bin/env python3
"""Render compact Markdown from Q-Crate iperf3 JSON measurements."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class IperfResult:
    """One endpoint-neutral iperf result used by the acceptance report."""

    name: str
    captured_utc: str
    protocol: str
    reverse: bool
    requested_mbps: float | None
    received_mbps: float
    loss_percent: float | None
    jitter_ms: float | None
    retransmits: int | None


RATE_SUFFIX = re.compile(r"-(\d+(?:\.\d+)?)([KMG])$", re.IGNORECASE)


def infer_requested_mbps(path: Path) -> float | None:
    """Infer an offered UDP rate from the documented result filename."""
    match = RATE_SUFFIX.search(path.stem)
    if match is None:
        return None
    multipliers = {"K": 1e-3, "M": 1.0, "G": 1e3}
    return float(match.group(1)) * multipliers[match.group(2).upper()]


def require_mapping(value: Any, context: str) -> dict[str, Any]:
    """Return a JSON object or reject a malformed iperf result."""
    if not isinstance(value, dict):
        raise ValueError(f"{context} is not a JSON object")
    return value


def number(value: Any, context: str) -> float:
    """Convert a required JSON number without accepting booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} is not numeric")
    return float(value)


def parse_iperf(path: Path) -> IperfResult:
    """Parse the receiver-visible summary from one iperf3 JSON file."""
    try:
        document = require_mapping(json.loads(path.read_text(encoding="utf-8")), "root")
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc

    if document.get("error"):
        raise ValueError(f"{path}: iperf3 reported {document['error']}")

    start = require_mapping(document.get("start"), "start")
    test = require_mapping(start.get("test_start"), "start.test_start")
    end = require_mapping(document.get("end"), "end")
    protocol = str(test.get("protocol", "")).upper()
    reverse = bool(test.get("reverse", False))
    timestamp = require_mapping(start.get("timestamp", {}), "start.timestamp")
    captured_utc = str(timestamp.get("time", "not-reported"))

    requested = test.get("target_bitrate")
    requested_mbps = infer_requested_mbps(path)
    if isinstance(requested, (int, float)) and not isinstance(requested, bool) and requested:
        requested_mbps = float(requested) / 1_000_000.0

    if protocol == "TCP":
        received = require_mapping(end.get("sum_received"), "end.sum_received")
        sent = require_mapping(end.get("sum_sent"), "end.sum_sent")
        return IperfResult(
            name=path.stem,
            captured_utc=captured_utc,
            protocol=protocol,
            reverse=reverse,
            requested_mbps=requested_mbps,
            received_mbps=number(
                received.get("bits_per_second"),
                "end.sum_received.bits_per_second",
            )
            / 1_000_000.0,
            loss_percent=None,
            jitter_ms=None,
            retransmits=int(number(sent.get("retransmits", 0), "retransmits")),
        )

    if protocol == "UDP":
        summary = require_mapping(end.get("sum"), "end.sum")
        sent_mbps = number(
            summary.get("bits_per_second"), "end.sum.bits_per_second"
        ) / 1_000_000.0
        loss_percent = number(summary.get("lost_percent"), "end.sum.lost_percent")
        received_mbps = sent_mbps * max(0.0, 1.0 - loss_percent / 100.0)
        return IperfResult(
            name=path.stem,
            captured_utc=captured_utc,
            protocol=protocol,
            reverse=reverse,
            requested_mbps=requested_mbps,
            received_mbps=received_mbps,
            loss_percent=loss_percent,
            jitter_ms=number(summary.get("jitter_ms"), "end.sum.jitter_ms"),
            retransmits=None,
        )

    raise ValueError(f"{path}: unsupported iperf protocol {protocol!r}")


def optional(value: float | int | None, suffix: str = "") -> str:
    """Format an optional table value."""
    if value is None:
        return "-"
    if isinstance(value, int):
        return f"{value}{suffix}"
    return f"{value:.3f}{suffix}"


def render(results: list[IperfResult], sources: list[Path]) -> str:
    """Render a deterministic, commit-friendly Markdown report."""
    generated = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    lines = [
        "# Q-Crate iperf3 measurement summary",
        "",
        f"Generated UTC: `{generated}`",
        "",
        "| Test | iperf time | Protocol | Mode | Requested | Received | Loss | Jitter | Retransmits |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{result.name}`",
                    result.captured_utc,
                    result.protocol,
                    "reverse" if result.reverse else "forward",
                    optional(result.requested_mbps, " Mb/s"),
                    optional(result.received_mbps, " Mb/s"),
                    optional(result.loss_percent, "%"),
                    optional(result.jitter_ms, " ms"),
                    optional(result.retransmits),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "Source files:",
            "",
            *[f"- `{path}`" for path in sources],
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="iperf3 JSON files")
    parser.add_argument("--output", type=Path, help="write Markdown instead of stdout")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        results = [parse_iperf(path) for path in args.inputs]
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from exc
    text = render(results, args.inputs)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
