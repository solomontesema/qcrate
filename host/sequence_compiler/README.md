# Q-Crate sequence compiler

This standard-library Python tool converts readable two-channel pulse intervals
into the canonical event table consumed by Linux, R5, and the PL sequencer. The
binary contract is defined in `common/sequence/README.md` and
`qcrate_sequence_format.h`.

The compiler, rather than RTL, resolves simultaneous edges and converts pulse
durations into complete output states. It rejects overlapping intervals on one
channel, out-of-range values, unknown or duplicate JSON keys, unsafe final
states, excessive event counts, and malformed binary images.

## Source format

```json
{
  "format": "qcrate-sequence-v1",
  "name": "two-channel-demo",
  "tick_hz": 200000000,
  "pulses": [
    {"name": "channel-0", "channel": 0, "start_tick": 0, "duration_ticks": 1000},
    {"name": "channel-1", "channel": 1, "start_tick": 400, "duration_ticks": 300}
  ]
}
```

Intervals are half-open: a pulse with start 10 and duration 5 is high at ticks
10 through 14 and low at tick 15. Pulses on different channels may overlap.
Pulses on the same channel may be adjacent but may not overlap; adjacent pulses
compile into one continuous high interval because no low tick exists between
them.

## Commands

Compile the tracked example into the ignored build directory:

```bash
python3 host/sequence_compiler/qcrate_sequence.py compile \
  host/sequence_compiler/examples/two_channel_demo.json \
  build/sequences/two_channel_demo.qseq
```

Validate and inspect the exact events:

```bash
python3 host/sequence_compiler/qcrate_sequence.py validate \
  build/sequences/two_channel_demo.qseq
python3 host/sequence_compiler/qcrate_sequence.py inspect \
  build/sequences/two_channel_demo.qseq
```

Run the focused unit tests:

```bash
python3 -m unittest discover -s host/sequence_compiler/tests -v
```

Expected example events are channel 0 high at tick 0, both channels high at
tick 400, channel 1 low at tick 700, and both channels low at tick 1000.

The `.qseq` file is deliberately small and deterministic. The target
`qcrate-sequence` client sends its header followed by one ordered event per
RPMsg request. Linux does not write the sequencer APB page directly; R5-0
validates the upload and owns that page.
