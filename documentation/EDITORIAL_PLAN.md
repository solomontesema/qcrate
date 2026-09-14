# Q-Crate Design Guide: agreed editorial plan

Status: agreed with the project owner on 14 September 2026.

This plan governs the next documentation work on the `documentation` branch.
It records the intended book structure, chapter responsibilities, source map
and writing sequence. It is an editorial document, excluded from
`chapters.txt` and the reader-facing PDF.

The starting documentation revision is `b9d290a`; its implementation baseline
is `d2d62fc50ec8e7b4e3643d8f6ea9c9cadcb83e86`. The existing eight-file draft
remains in place until the source audit and planned reorganisation. The eleven
chapters below are the agreed destination, not a claim that they are written.

## Purpose and readership

Develop a self-contained engineering book that follows a measurement from its
mathematical definition through FPGA arithmetic, heterogeneous control, memory
ownership and transport to a trustworthy result. The intended readers include
Master-2 electronics and embedded-systems students and practising engineers.

Assume basic digital logic, HDL, programming and elementary signals knowledge.
Explain the additional DSP and system concepts where needed; do not assume
prior mastery of OpenAMP, DMAEngine, complex baseband or fixed-point DSP.
The narrative must remain useful without requiring the reader to open source
files to understand every paragraph.

The implemented instrument can support a future course or internship. A full
course additionally needs scoped laboratories, prerequisites and assessment.
Those teaching materials can become a companion later; their creation is not
part of this first book reorganisation.

## Agreed reading order

The preface, glossary and references become unnumbered supporting material.
Detailed register tables, protocol layouts and long setup procedures may be
placed in appendices and referenced from their owning chapters.

| Chapter | Purpose and owned content | Reader preparation |
|---|---|---|
| 1. Q-Crate and the Measurement Problem | Pulse, shot, acquisition and response; synthetic reference application; simple end-to-end overview; demonstrated capabilities and physical limitations | Basic engineering background |
| 2. Sampled Signals and Complex Baseband | Sampling, aliasing, discrete frequency, real and complex spectra, phasors, I/Q, carrier and LO, mixing, magnitude, phase and constellation | Chapter 1; elementary algebra and signals |
| 3. Filtering, Rate Conversion and Finite Precision | Convolution, FIR response, bandwidth, decimation, alias rejection, group delay, transients, fixed-point representation, rounding, saturation and coefficient quantisation | Chapter 2 |
| 4. System Architecture and Interface Contracts | PL/R5/A53/host partitioning; control and data paths; domain-level clocks and resets; ownership; configuration identity; lifecycle of a complete measurement | Chapters 1-3 |
| 5. FPGA Timing and Control | Implemented timebase, sequencer, event memory, triggers, APB registers, command/status CDC, resets, interrupts and atomic configuration activation | Chapter 4; basic RTL |
| 6. FPGA Signal Processing and Streaming | Actual synthetic source, NCO, mixer, FIR backend, quantiser, pipeline composition, sample acceptance, backpressure, framing and timestamps; exact arithmetic widths and timing | Chapters 2-5 |
| 7. R5 Firmware and Heterogeneous Control | FreeRTOS service organisation; remoteproc/OpenAMP/RPMsg; command ABI; sequence transactions; configuration validation; bounded waits and recovery | Chapters 4-5; basic C |
| 8. Linux Acquisition and DMA Ownership | DMAEngine, scatter-gather descriptors, coherent memory, userspace mapping, bank lifecycle, completion, starvation, cancellation and recovery | Chapters 4-7; basic C and Linux |
| 9. Network Transport and Durable Recording | Sender concurrency, packetisation, UDP protocol, integrity, reassembly, journal, QIDX publication and incomplete-shot handling | Chapters 4 and 8 |
| 10. Host Analysis and Reproducible Experiments | Profile resolution, numerical identity, reference models, FFT interpretation, coherent estimation, analyzer, automation and LO sweeps | Chapters 2-4 and 7-9; basic Python |
| 11. System Integration and Measured Characterisation | Assembly and operation of the layers; reproducible measurement setups; accepted results; throughput limits, recovery observations and evidence interpretation | Earlier chapters as needed for each experiment |

Chapter 11 contains actual experiments and results. It does not reinstate the
old generic Verification Strategy or Development Roadmap chapters. Block-level
verification belongs beside the block it verifies.

## Boundaries that prevent duplication

Each detailed contract has one authoritative home. Other chapters explain its
local implications and refer to that home instead of duplicating the contract.
Appendices, if used, are extensions of that ownership rather than second copies.

| Topic | Authoritative treatment | Treatment elsewhere |
|---|---|---|
| System partitioning and ownership | Chapter 4: responsibilities, boundaries and complete shot lifecycle | Implementation chapters show how those responsibilities are enforced |
| Hardware timebase, trigger and timestamp generation | Chapter 5: event meanings and generation rules | Chapter 6 explains sample alignment; chapters 9-10 explain transport and interpretation |
| Sample format and numerical contract | Chapter 6: exact implemented widths, signs, scaling, rounding and packing | Chapters 2-3 derive concepts; chapters 9-10 consume the defined representation |
| Clock/reset and CDC implementation | Chapter 5: concrete crossings and reset behaviour | Chapter 4 shows domains; chapter 6 explains relevant pipeline consequences |
| Sequence and runtime activation commands | Chapter 7: software transaction semantics and R5 policy | Chapter 5 owns PL enforcement; chapter 10 owns host profile resolution |
| Configuration identity derivation | Chapter 10: canonical resolution, DSP identity and full-profile identity | Chapter 4 explains its system role; chapters 5, 7-9 document capture, validation and carriage |
| DMA bank lifecycle and userspace ABI | Chapter 8: descriptors, mapping, completion, tokens and release | Chapter 4 states the invariant; chapter 9 explains sender use of a held bank |
| Network and storage contracts | Chapter 9: wire format, journal, reassembly and publication | Chapter 10 reads only published measurements and interprets integrity state |
| Integrated acceptance results | Chapter 11: setup, source revision, method, observations and limits | Subsystem chapters retain their own focused verification evidence |

For example, the architecture explains why DDR banks are owned; the FPGA
chapter specifies stream guarantees; the Linux chapter implements capture
ownership; the network chapter explains when a sender may release a bank.
These are different questions, not four copies of a DMA tutorial.

## DSP scope and distinctions

Broaden the foundations around the actual measurement path and its analysis.
Use worked examples with Q-Crate's numerical parameters, while distinguishing
mathematical examples from implementation and hardware measurements.

Explicitly address:

- sample rate versus FPGA clock rate;
- mathematical filter group delay versus implementation latency;
- FFT bin spacing versus useful spectral resolution;
- complex spectral direction and the sign of Q;
- startup transients versus steady-state filter response;
- fixed-point agreement versus analogue accuracy;
- nominal stream payload rate versus measured sustained acquisition throughput.

Interpolation, DUC, CIC filters and alternative converter interfaces may appear
as short extensions or comparisons when they clarify the design. Do not imply
that they are implemented Q-Crate capabilities without source evidence.

## Source-to-chapter map

All paths below are relative links into the repository. They identify audit
starting points, not proof that every statement in those documents is current.
Before writing an as-built claim, inspect the relevant implementation, tests
and evidence at a recorded commit. Resolve contradictory README or milestone
language against that evidence; never silently convert a proposal to a fact.

| Destination | Principal source material | Evidence or audit focus |
|---|---|---|
| 1 | [Project overview](../README.md), [release notes](../releases/v1.0.0.md) | [Accepted run evidence](../host/acceptance/evidence/dp5d-kv260-accepted.json); distinguish synthetic digital validation from physical measurements |
| 2 | [DSP model](../host/dsp_model/qcrate_dsp.py), [reference model](../host/dsp_model/qcrate_dsp_reference.py), [DSP explanation](../host/dsp_model/README.md) | [Model tests](../host/dsp_model/tests); verify conventions and worked examples independently |
| 3 | [DSP design rationale](../rtl/dsp/README.md), [coefficient/table manifest](../rtl/dsp/tables/manifest.json), [quantiser RTL](../rtl/dsp/qcrate_fir_quantizer.sv) | Quantised filter response, decimation phase, transient handling, numerical ranges and model agreement |
| 4 | [KV260 hardware](../kv260/hw/README.md), [block design](../kv260/hw/bd), [core integration](../kv260/hw/rtl/qcrate_core.sv), [shared contracts](../common) | Trace a shot across actual interfaces; confirm clocks, reset domains, ownership and identity boundaries |
| 5 | [Control RTL](../kv260/hw/rtl), [sequence format](../common/sequence/qcrate_sequence_format.h), [DSP register contract](../common/config/qcrate_dsp_registers.json) | [Hardware testbenches](../kv260/hw/tb): sequencing, APB, CDC, configuration and stream control |
| 6 | [DSP RTL and vendor backend](../rtl/dsp), [stream engine](../kv260/hw/rtl/qcrate_stream_engine.sv), [core](../kv260/hw/rtl/qcrate_core.sv) | [DSP testbenches](../rtl/tb), [reference model](../host/dsp_model/qcrate_dsp_reference.py); numerical tables, acceptance cadence and latency |
| 7 | [R5 service](../kv260/r5_freertos/qcrate_rpmsg_service.c), [protocol ABI](../common/protocol/qcrate_protocol.h), [semantic validation](../common/config/qcrate_runtime_config.c), [Linux OpenAMP client](../kv260/linux/openamp), [Vitis flow](../kv260/vitis) | [OpenAMP platform and acceptance notes](../kv260/linux/openamp/README.md), [configuration tests](../common/config/tests), [Linux tool tests](../kv260/linux/tests) |
| 8 | [DMA ownership guide](../kv260/linux/dma/README.md), [DMA UAPI](../common/dma/qcrate_dma_uapi.h), [kernel driver](../kv260/linux/petalinux/qcrate-kv260/project-spec/meta-user/recipes-modules/qcrate-dma/files/qcrate_dma.c) | Coherent allocation/mapping, exclusive owner, finite-SG pool, token validation, timeout/cancel and recorded board results |
| 9 | [Streamer](../kv260/linux/data_plane/qcrate_streamer.c), [shared data plane](../common/data_plane), [recorder](../host/data_plane/qcrate_recorder.c), [run format](../host/data_plane/RUN_FORMAT.md) | [Protocol tests](../common/data_plane/tests), [host ingest tests](../host/data_plane/tests); packet integrity, publication order and release semantics |
| 10 | [Analyzer](../host/analyzer), [experiment profiles and sweeps](../host/experiment_profiles), [sequence compiler](../host/sequence_compiler), [DSP model](../host/dsp_model) | Resolved/captured identity, bit-exact comparison, FFT scaling, coherent estimator, sample selection and corresponding subsystem tests |
| 11 | [Application acceptance](../host/acceptance), [sweep procedure and results](../host/experiment_profiles/README.md), [release manifest](../releases/v1.0.0-manifest.json), [platform build/deployment](../kv260/README.md), [PetaLinux setup](../kv260/linux/petalinux/README.md) | Reproducible setup and tool versions; historical versus newly run measurements; throughput, recovery and limitations |

Theoretical derivations additionally need appropriate primary documentation or
textbook references. Project comments alone are not authority for general DSP,
bus or operating-system behaviour. Record exact source versions when writing
those sections.

## Migration from the current draft

| Existing source | Intended destination and action |
|---|---|
| `chapters/01-preface.md` | Unnumbered preface; retain conventions and revision history |
| `chapters/02-introduction.md` | New chapter 1; sharpen the measurement problem and implemented scope |
| `chapters/03-system-architecture.md` | Chapter 4 owns the architectural narrative; move concrete control/CDC details to 5, DSP/stream details to 6 and software mechanics to 7-9 |
| `chapters/04-signal-processing.md` | Expand into chapters 2-3; move implementation-specific verification into 6 where appropriate |
| `chapters/05-fpga-implementation.md` | Replace obsolete proposals with audited implementation across 5-6 and DMA material in 8; distribute verification beside its subject |
| `chapters/06-firmware-software.md` | Expand into 7-10; retain only system-level responsibilities in 4 and integrated results in 11 |
| `chapters/07-glossary.md`, `chapters/08-references.md` | Unnumbered supporting material |

Do not create empty chapters or change the PDF manifest merely to make the
destination structure appear complete. Once the audit identifies usable content,
perform the planned reorganisation in one coherent change, update the chapter
index and links, and rebuild the PDF. Further restructuring should respond to
a concrete reading or evidence problem, with its reason recorded here.

## Writing sequence and completion criteria

1. **Outline and boundaries:** this plan records the agreed scope and ownership.
2. **Audit the baseline:** prepare a claim-to-source ledger with commit, owning
   chapter, implementation location, relevant test/evidence and unresolved
   contradictions. The audit is the next task; this source map is its input.
3. **Reorganise once:** migrate reusable text after the audit, update the index
   and build order, preserve the original guide entry link and verify the PDF.
4. **Write architecture first:** develop chapter 4 and one complete measurement
   walkthrough. Writing order differs from reading order so the system facts
   anchor the rest of the book.
5. **Develop foundations with implementation:** write chapters 2-3 alongside
   chapter 6, checking examples, arithmetic and observed behaviour together.
6. **Expand the remaining implementation:** complete chapters 5 and 7-10 using
   the agreed ownership boundaries.
7. **Assemble and review:** complete chapter 11, reconcile chapter 1, supporting
   material and references, then review consistency and rendering end to end.

For each subsystem, explain the engineering problem and concept, the implemented
solution and design rationale, its exact interface/numerical/timing behaviour,
a worked example and meaningful failure case, and the verification evidence
with its limits. This is a coverage guide, not a mandatory repetitive set of
headings. Write connected book prose rather than instructions to an AI or a
chronicle of development milestones.

A chapter is technically reviewed when its as-built claims are traceable to
the recorded baseline, examples and units agree, relevant failure behaviour is
covered, proposals are not masquerading as implementation, duplicate contract
descriptions are removed, and citations and rendered figures/tables are checked.
Tests present in the repository are not automatically evidence of a hardware
pass. Report historical measurements as historical, and distinguish any new
checks actually performed during documentation work.

## Professor review edition

For the meeting in roughly two weeks, target a coherent review edition: a clear
contents plan, strong architecture explanation, one well-developed teaching
example and demonstrated results. This is not a commitment to finish an entire
course-quality book by that meeting. Identify unfinished sections honestly.

Work remains documentation-focused while physical converter access is pending.
Simulation/model checks may support explanations; FPGA, firmware or OS rebuilds
are not required merely to reorganise or write this book. New implementation
work is a separate scope decision.
