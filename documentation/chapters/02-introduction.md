# Introducing Q-Crate

## Motivation

Modern instrumentation often needs the same combination of capabilities:

- deterministic signal generation and acquisition;
- low-latency digital signal processing;
- software-configurable control and monitoring;
- sustained movement of large sample streams;
- synchronisation between hardware, firmware, and host software; and
- enough observability to debug the complete chain.

Commercial platforms can solve these problems, but they often hide the details
that an engineer needs to learn or constrain the system to a fixed workflow.
Q-Crate approaches the problem from the opposite direction. It begins as an
engineering platform whose internal data path is deliberately understood,
implemented, measured, and documented.

The name **Q-Crate** is currently a project name. The architecture is not tied
to a single application. Possible future uses include laboratory data
acquisition, waveform generation, quantum-control experiments, LiDAR or radar
prototyping, and general FPGA-based instrumentation.

## Project goals

The near-term engineering goals are:

1. establish a clean Zynq UltraScale+ hardware/software architecture;
2. generate and process deterministic streaming data in programmable logic;
3. control the datapath through memory-mapped registers;
4. transfer samples to processor memory through AXI DMA;
5. observe and analyse results from software, including Python tools;
6. develop reusable DSP blocks such as an NCO, mixer, filters, decimators, and
   interpolators; and
7. verify each block in simulation before integrating it on hardware.

The project is also a structured learning vehicle for SystemVerilog, AXI,
DMA, fixed-point DSP, embedded Linux, FreeRTOS, and heterogeneous processing.

## First reference application

The first integrated application is the **Networked Pulsed-IQ Analyzer**. An
R5-supervised PL sequence produces deterministic excitation and acquisition
events. The DSP path creates a coherent IQ response, DMA moves each triggered
shot into explicitly owned memory, and the versioned network data plane sends
it to a host for time-domain, magnitude, phase, and spectral analysis.

The first implementation uses a deterministic RTL source and channel model.
This is deliberate: it makes every sample and timestamp verifiable before an
analogue front end introduces clock, noise, and converter uncertainties. A
later ADC replaces the source while preserving the control, timing, DMA,
network, recording, and analysis architecture.

The application establishes an important data-integrity rule for future
acquisition buffers:

```text
FREE -> FILLING -> READY -> USER_OWNED -> FREE
```

Unread measurement data is never overwritten silently. A resource shortage is
reported as starvation or a skipped acquisition rather than hidden by stale or
torn data.

## Non-goals of Version 0.1

This edition does not claim a finished instrument, a frozen external interface,
or measured analogue performance. ADC/DAC selection, RF front-end design,
multi-channel synchronisation, and product-level mechanical packaging remain
future topics.
