# Q-Crate experiment profiles

DP-6 moves Q-Crate from one compiled experiment to runtime-selectable,
repeatable experiments. DP-6A defines the contract before adding writable RTL:
an operator supplies physical values, the compiler resolves them into the exact
integers hardware will consume, and deterministic identities bind those values
to the numerical, acquisition, and timing contracts.

This directory contains the human profile schema, compiler, examples, and
focused tests. DP-6B implements the corresponding PL shadow registers and
atomic activation mailbox. It deliberately does not yet expose direct host
configuration: DP-6C gives R5 semantic ownership, and DP-6D exposes that owned
workflow through the existing control tools.

## Experiment definition

A `qcrate-experiment-profile-v1` document has four parts:

| Part | Purpose |
|---|---|
| `dsp` | Synthetic input and DDC LO frequency, phase, amplitude, and repeatable noise |
| `acquisition` | Existing DSP stream mode, words per frame, and frames per shot |
| `sequence` | Repository-relative pulse-sequence source compiled into the shot timing image |
| `name` | Human-readable metadata; deliberately excluded from identity |

The source schema is
[`qcrate_experiment_profile.schema.json`](qcrate_experiment_profile.schema.json).
The compiler performs the same checks directly, so no separate JSON Schema
package is required.

The current numerical contract remains fixed at a 200 MS/s input rate, a
decimation factor of 16, and a 12.5 MS/s complex output. Those are properties
of the implemented DSP architecture rather than runtime settings. The initial
profile intentionally does not select arbitrary source providers or feature
flags.

## Resolution and identity

`qcrate_profile.py compile` converts human decimal values into:

- 32-bit NCO phase increments and initial phases;
- signed Q1.15 amplitudes and a 16-bit nonzero noise seed;
- the existing integer frame contract;
- the exact compiled sequence image hash, CRC, event count, and tick rate;
- ordered future APB shadow-register writes; and
- deterministic DSP and full-profile identities.

Canonical identity input is sorted, whitespace-independent JSON containing only
resolved semantics. File paths and display names do not affect identity.
The encoding is UTF-8/ASCII JSON with sorted keys and compact separators. The
DSP SHA-256 covers the resolved DSP bundle and numerical contract; its first
eight digest bytes, interpreted in network display order, form the 64-bit ID.

The 64-bit **DSP configuration ID** identifies the active DSP integers plus the
fixed numerical/table contract. It fits the existing Data Plane v1 `config_id`
field and is intended for fast correlation, not as a cryptographic signature.
The complete DSP SHA-256 is retained in the resolved document.

The **experiment profile SHA-256** additionally binds acquisition framing and
the compiled pulse-sequence image. It is the durable identity recorded in run
metadata. Changing only frame count leaves the DSP ID unchanged but changes the
full profile identity. Every LO point in a future sweep therefore receives a
different resolved DSP ID and full profile identity.

## Atomic ownership contract

The minimal future register definition is
[`common/config/qcrate_dsp_registers.json`](../../common/config/qcrate_dsp_registers.json).
Page `0x3000` contains shadow fields, active readback fields, `COMMIT`,
`DISCARD`, status, rejection reason, and an active generation counter. This is
an explicit cross-language contract, not a general register-generation system.

The intended lifecycle is:

```text
host profile -> R5 semantic validation -> APB shadow writes
             -> PL structural/state check -> coherent CDC commit
             -> all active DSP fields change together at an idle boundary
```

R5 is authoritative for semantic policy. The PL enforces only register widths,
reserved bits, commit integrity, and safe state boundaries. A commit while the
instrument is armed or running is rejected; it is not silently deferred.

DP-6 intentionally uses one active configuration per recorded run. Retuning
closes the current run, commits a new profile, and starts a new run. The
identity model and wire protocol do not prevent a later profile-table or
per-shot implementation.

## Commands

Resolve one profile and inspect its exact values without writing a file:

```bash
python3 host/experiment_profiles/qcrate_profile.py show \
  host/experiment_profiles/examples/lo_29mhz.json
```

Compile a durable resolved profile:

```bash
python3 host/experiment_profiles/qcrate_profile.py compile \
  host/experiment_profiles/examples/lo_29mhz.json \
  build/experiments/lo_29mhz.resolved.json
```

Run the DP-6A visible proof. The same 30 MHz synthetic source must appear near
1.0 MHz with a 29 MHz LO and near 1.5 MHz with a 28.5 MHz LO:

```bash
python3 host/experiment_profiles/qcrate_profile.py prove \
  host/experiment_profiles/examples/lo_29mhz.json \
  host/experiment_profiles/examples/lo_28_5mhz.json \
  --output build/experiments/dp6a-proof.json
```

Run the focused contract tests:

```bash
python3 -m unittest discover -s host/experiment_profiles/tests -v
```

No Vivado, Vitis, PetaLinux, or board build is required for DP-6A.

## DP-6A accepted proof

The tracked [proof summary](examples/dp6a-proof.json) records the model result:

| Profile | LO tuning word | DSP config ID | Resolved baseband | Model FFT peak |
|---|---:|---:|---:|---:|
| 29 MHz LO | `0x251eb852` | `0x5db4fb578b27b09f` | 999999.978 Hz | 999630.906 Hz |
| 28.5 MHz LO | `0x247ae148` | `0xaf46287bb969ed24` | 1499999.966 Hz | 1500984.252 Hz |

Both FFT peaks are within one 3075.787 Hz bin of the frequency predicted from
the exact tuning words. The corresponding [resolved profiles](examples/resolved/)
are tracked as readable golden contract artifacts.

## DP-6B hardware boundary

The implemented `0x3000` APB page stores a complete shadow bundle in the
100 MHz control domain. `COMMIT` snapshots that bundle into an acknowledged
`xpm_cdc_handshake` request. The 200 MHz destination waits an additional clock
before checking the experiment boundary, then either activates every field and
increments `ACTIVE_GENERATION` in one edge or rejects the request without
changing any active field. A second acknowledged handshake returns the result
and coherent active readback to the control domain.

Safe activation requires both the stream engine and sequence engine to be idle
and unarmed, with no visible command pulse. This closes the race between a
configuration commit and a simultaneous arm/start command. Rejection is
explicit and never queues a hidden deferred update.

The stream engine snapshots the active 64-bit DSP configuration ID when a
capture starts. The captured identity is returned at stream offsets `0x04c`
and `0x050`, independently of later shadow edits. DP-6C will use this hardware
boundary through R5 rather than granting Linux applications unrestricted
semantic ownership.

For both the active and captured 64-bit identities, software reads the low
word first; that access latches the corresponding high word for the following
read. A coherent active-bundle inspection also reads `ACTIVE_GENERATION`
before and after the fields and retries if the generation changed.
