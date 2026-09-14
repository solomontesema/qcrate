# Preface

Q-Crate is a learning-driven FPGA instrumentation project with the potential to
grow into a reusable high-speed acquisition, signal-processing, and control
platform. This guide records both the engineering foundations and the concrete
implementation decisions behind the project.

The document has three purposes:

1. to explain the digital-design and signal-processing concepts needed to
   understand the system;
2. to document the Q-Crate architecture and implementation as they evolve; and
3. to preserve design reasoning, verification methods, and lessons learned.

This is a **living engineering document**. Statements marked *implemented*
describe the current design baseline. Statements marked *planned* describe the
intended direction and may change after simulation or hardware experiments.

**Version 0.2-draft scope.** This chapter-based working edition begins
reconciling the guide with repository baseline `d2d62fc` (September 2026).
The software overview is updated; the inherited FPGA proposal, architecture
and DSP detail still require review. Digital acceptance does not establish
physical ADC/DAC or analogue performance.

## Document conventions

| Convention | Meaning |
|---|---|
| **Implemented** | Present in the established Q-Crate design baseline |
| **Planned** | Intended architecture, not yet claimed as verified hardware |
| `signal_name` | RTL signal, register, filename, command, or identifier |
| $x[n]$ | Discrete-time signal indexed by sample number $n$ |
| $f_s$ | Sampling frequency in samples per second |

## Revision history

| Version | Date | Summary |
|---|---|---|
| 0.1 | 2026-08 | Initial architecture and DSP foundations |
| 0.2-draft | 2026-09 | Chapter sources, revised software overview, validation material redistributed; full technical refresh in progress |

\newpage
