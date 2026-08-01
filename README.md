# Q-Crate

Q-Crate is a distributed real-time pulse-control, IQ acquisition, streaming, and telemetry platform built around the KV260, PYNQ-Z2, STM32 B-U585I-IOT02A, and a Linux host.

## Start
1. Read `AGENTS.md` and `docs/INDEX.md`.
2. Copy `config/build.json.example` to `config/build.json`.
3. Export the validated Vivado BD as Tcl and set its path in the config.
4. Run `python3 scripts/build.py --stage project`.

That command creates the project and BD products only; it does not synthesize.

## HDL policy
All handwritten RTL and tests use SystemVerilog (`.sv`). Generated vendor sources stay in their generated language.
