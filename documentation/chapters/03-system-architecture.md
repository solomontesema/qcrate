# System Architecture

Q-Crate is implemented as a heterogeneous instrumentation system in which processing responsibilities are divided between programmable logic, real-time firmware, embedded Linux, and host software. The first hardware platform is the AMD Kria KV260, built around the Kria K26 system-on-module and its Zynq UltraScale+ MPSoC.

The architecture follows a simple principle: operations whose timing is defined in FPGA clock cycles are performed in programmable logic, bounded real-time supervision is performed by the R5 processor, operating-system services and bulk data movement are handled by the A53 processors running Linux, and computationally heavier analysis and visualisation are performed on the host computer.

This separation is particularly important for instrumentation. Generating a pulse at an exact hardware timestamp, accepting a sample without violating a streaming handshake, allocating a DMA buffer, transmitting a UDP packet, and plotting an FFT are all necessary operations, but they have very different timing requirements. Q-Crate therefore does not attempt to perform them all in one processing environment.

The current reference application, the **Networked Pulsed-IQ Analyzer**, exercises this architecture from deterministic event generation in the programmable logic to IQ analysis on a networked host.

## Hardware platform

The Kria K26 contains a Zynq UltraScale+ MPSoC, which combines several computing resources in one device:

- **programmable logic (PL)** containing FPGA logic, memories and DSP resources;
- **quad-core Arm Cortex-A53 processors**, used by Q-Crate to run embedded Linux;
- **dual Arm Cortex-R5F real-time processors**, of which R5-0 is used by the current Q-Crate real-time control service;
- DDR memory accessible through the processing system;
- PS-to-PL and PL-to-PS AXI interfaces; and
- timers, interrupt controllers, Ethernet and other processing-system peripherals.

The presence of both processors and programmable logic allows Q-Crate to be partitioned according to timing requirements rather than according to programming language.

The PL is responsible for the deterministic signal and timing path. A hardware action in this domain can be related directly to the 200 MHz system clock, whose period is

$$
T_{\mathrm{clk}} = \frac{1}{200\ \mathrm{MHz}} = 5\ \mathrm{ns}.
$$

Consequently, a timing event separated by 100 clock ticks represents an interval of exactly 500 ns in the digital timing model.

Linux on the A53 processors operates on a completely different timescale. It provides memory management, device drivers, filesystems, Ethernet networking and user applications, but normal Linux task scheduling does not guarantee execution on a particular 5 ns clock boundary. Q-Crate therefore uses Linux for supervisory and transport functions rather than for waveform-level timing.

The R5 processor occupies an intermediate position. It executes software, but its FreeRTOS environment is substantially more deterministic than general-purpose Linux. In the present architecture R5-0 supervises the pulse sequencer and provides the software ownership boundary for sequence upload and lifecycle operations.

## Architectural partitioning

The implemented Q-Crate system can be viewed as four cooperating execution domains.

| Domain | Main responsibility | Timing character |
|---|---|---|
| Programmable logic | Sequencing, timebase, DSP and AXI4-Stream generation | Clock deterministic |
| R5-0 / FreeRTOS | Real-time sequence supervision and validation | Bounded real-time software |
| A53 / Linux | DMA, memory-buffer ownership, networking and system services | General-purpose software |
| Host computer | Configuration, recording, visualisation and numerical analysis | Non-real-time |

These domains form a hierarchy rather than four independent computers.

At the lowest level, programmable logic executes events and produces samples. R5 controls the deterministic sequence without generating the individual pulse timings in software. Linux manages the much larger memory and networking environment around each acquisition. Finally, the host receives measurement records and interprets them as time-domain or frequency-domain data.

A simplified end-to-end view is:

```text
                            HOST COMPUTER
                  recording, analysis, visualisation
                               ^
                               |
                         UDP Data Plane v1
                               |
                               |
+------------------------------------------------------------------+
|                         KV260 / KRIA K26                         |
|                                                                  |
|  +-------------------------- A53 LINUX -----------------------+   |
|  | DMA ownership | DDR buffers | networking | system policy  |   |
|  +-------------+----------------------------+-----------------+   |
|                |                            |                     |
|                | DMA                        | RPMsg/OpenAMP        |
|                |                            v                     |
|                |                    +---------------+             |
|                |                    | R5-0/FreeRTOS |             |
|                |                    | sequence      |             |
|                |                    | supervision   |             |
|                |                    +-------+-------+             |
|                |                            | APB control          |
|                v                            v                     |
|  +------------------------------------------------------------+  |
|  |                  PROGRAMMABLE LOGIC                        |  |
|  |                                                            |  |
|  |  sequence engine ---- shared 200 MHz timebase              |  |
|  |                                                            |  |
|  |  synthetic source -> DDC -> FIR decimator -> stream engine |  |
|  |                                              |             |  |
|  +----------------------------------------------|-------------+  |
|                                                 | AXI4-Stream   |
|                                                 v               |
|                                            AXI DMA S2MM         |
|                                                 |               |
|                                                 v               |
|                                                DDR              |
+------------------------------------------------------------------+
```

The diagram also shows an important distinction between **supervision** and **execution**. R5 supervises a sequence, but the PL sequence engine executes its event timings. Linux manages an acquisition, but the PL and DMA move the actual sample stream without depending on Linux to service every individual sample.

## Control plane and data plane

Q-Crate separates low-bandwidth configuration traffic from high-bandwidth measurement traffic.

These two traffic classes are referred to as the **control plane** and the **data plane**.

The control plane carries commands and state. Examples include loading a sequence, setting a frame length, starting an acquisition, reading a status register and acknowledging an interrupt.

The internal data plane carries sample streams. Once an acquisition begins, potentially millions of sample values must move through the system without requiring a processor transaction for every sample.

| Property | Control plane | Internal data plane |
|---|---|---|
| Information | Commands, configuration and status | Measurement samples |
| Typical traffic | Occasional register accesses | Sustained stream |
| PL interface | APB | AXI4-Stream |
| Memory transfer | Not normally required | AXI DMA |
| Primary concern | Correct command semantics | Throughput and back-pressure |

This separation is fundamental to the architecture. A processor may require many clock cycles or even microseconds to issue a software command, whereas an FPGA datapath can accept or produce a value on every 200 MHz clock cycle.

Attempting to control individual samples through software-visible registers would therefore couple measurement throughput to processor latency. Q-Crate instead configures a hardware operation through the control plane and allows the resulting sample sequence to travel independently through the streaming data path.

At the network boundary the term *data plane* is also used for **Q-Crate Data Plane v1**, the versioned UDP wire protocol used between the KV260 and the host. This network protocol is distinct from AXI4-Stream, although both participate in transporting the same measurement data at different stages of the system.

## KV260 block design

The Vivado block design provides the infrastructure connecting the Zynq processing system, Q-Crate RTL and DDR memory.

![Current Q-Crate KV260 Vivado block design](../../kv260/hw/bd/kv260_bd.png){width=100%}

*Figure: Current reproducible KV260 Vivado block design. The corresponding exported `design_1.tcl` file is the reproducible source representation of the diagram.*

The block design contains two major paths: a processor-controlled register path and a DMA payload path.

The control path is:

```text
PS M_AXI_HPM0_FPD
        |
        v
control SmartConnect
        |
        +------> AXI DMA AXI-Lite control
        |
        +------> AXI-to-APB bridge
                        |
                        v
                   qcrate_core
```

The processor accesses the custom Q-Crate register space through an AXI-to-APB bridge. The AXI side belongs to the Zynq processing-system infrastructure; the APB side is implemented as part of the Q-Crate RTL interface.

The DMA payload path is:

```text
qcrate_core
    |
    | AXI4-Stream
    v
AXI DMA S2MM
    |
    +---- M_AXI_S2MM ----+
    |                    |
    +---- M_AXI_SG ------+----> data SmartConnect
                                      |
                                      v
                                  PS HP0
                                      |
                                      v
                                     DDR
```

**S2MM** means *stream to memory-mapped*. Samples arrive at the DMA as an AXI4-Stream and are written to addresses in DDR memory.

The DMA also uses an independent scatter-gather master interface, `M_AXI_SG`, to read DMA descriptors. This allows a sequence of memory buffers to be prepared before an acquisition begins instead of requiring software to reprogram the DMA after every frame.

The payload and descriptor interfaces share the PS high-performance memory path through the data SmartConnect.

## Clock architecture

Two primary PL clock domains are used.

The **control domain** operates at 100 MHz and is driven by `pl_clk1`. APB transactions and most software-visible register logic operate in this domain.

The **stream and timing domain** operates at 200 MHz and is driven by `pl_clk0`. The sample pipeline, AXI4-Stream source, DSP chain, sequence engine and 64-bit hardware timebase operate in this domain.

```text
100 MHz                                         200 MHz
control domain                                  stream/timing domain

APB registers  ---- command CDC -------------> stream engine
      |                                          DSP chain
      |                                          sequence engine
      |                                          timebase
      |
      <---- status/event CDC ------------------
```

The two domains exist because their tasks have different requirements. Control transactions do not benefit from operating at the full signal-processing clock rate, while the datapath and timing engine require the higher-frequency domain.

An earlier form of the Q-Crate block design included an external AXI Clock Converter in the control path. The current architecture does not require it. The processor-side control SmartConnect, DMA AXI-Lite interface and AXI-to-APB bridge all operate in the 100 MHz control domain.

The AXI DMA is the boundary between the 100 MHz control interface and the 200 MHz streaming interface. It is configured for asynchronous operation so that its control and payload interfaces can use the appropriate clocks without an additional external converter in the APB branch.

The custom RTL nevertheless contains genuine clock-domain crossings because commands and status must travel between the 100 MHz APB domain and the 200 MHz execution domain.

## Clock-domain crossing inside `qcrate_core`

A signal sampled by logic driven from an unrelated clock can violate setup or hold requirements and enter a metastable state. Clock-domain crossing, usually abbreviated **CDC**, is therefore an architectural concern rather than merely a wiring detail.

Q-Crate uses different CDC structures according to the type of information being transferred.

Single-bit asynchronous state uses synchronising flip-flops. Command transactions carrying several related fields use a handshake in which the source retains the complete value while an event indication crosses the boundary. Status information moving in the opposite direction is transferred as a coherent snapshot rather than as independently synchronised bits.

The sequencer also uses memory that is accessible from both clock domains. Software-visible accesses occur from the control side, while the sequence engine reads events from the 200 MHz execution side.

The result is a clear ownership model:

```text
100 MHz domain                        200 MHz domain

configuration
      |
      | stable command + handshake
      +------------------------------>
                                       execute
                                          |
                                          | coherent status snapshot
      <-----------------------------------+
      |
software-visible status
```

This prevents a multi-bit value such as a frame length, event count or timestamp from being observed as a mixture of old and new bits.

## RTL hierarchy

The handwritten programmable-logic design is implemented primarily in SystemVerilog.

The top-level integration module is:

```text
kv260/hw/rtl/qcrate_top.sv
```

`qcrate_top` connects the Vivado-generated block design to the custom Q-Crate RTL. Its principal interfaces are the APB control bus, AXI4-Stream output, the two PL clocks and resets, and the interrupt connection back toward the processing system.

Below this integration layer is:

```text
kv260/hw/rtl/qcrate_core.sv
```

`qcrate_core` contains the main instrument logic. Its major subsystems can be represented as:

```text
qcrate_core
|
+-- APB fabric
|   |
|   +-- system registers
|   +-- stream registers
|   +-- sequence registers
|
+-- control/stream CDC
|
+-- stream subsystem
|   |
|   +-- DSP chain
|   +-- stream engine
|
+-- deterministic sequence subsystem
|   |
|   +-- sequence event RAM
|   +-- sequence engine
|   +-- 64-bit timebase
|
+-- status/event CDC
|
+-- interrupt controller
```

The APB fabric divides the Q-Crate register space into 4 KiB pages. Page zero contains system identification and general registers, page one contains stream-control registers, and page two contains sequencer registers and access to sequence-event memory.

The SystemVerilog hierarchy separates the custom instrument logic from the Vivado block design. The generated design therefore provides processing-system and AXI infrastructure, while modules such as the sequencer, stream engine, CDC logic and register banks remain ordinary RTL that can be simulated independently.

Earlier Q-Crate sources used the filename `qcrate_top.v`. The active implementation is now `qcrate_top.sv`, and both the top-level integration and the custom core are SystemVerilog modules.

## Streaming and DMA architecture

The stream engine exposes a 32-bit AXI4-Stream master interface to the DMA.

For every transfer, the interface includes:

- `TDATA`, containing the sample word;
- `TKEEP`, indicating valid bytes;
- `TVALID`, asserted when the source presents valid data;
- `TREADY`, asserted when the DMA can accept data; and
- `TLAST`, marking the final word of a frame.

A transfer occurs only on a clock edge for which both `TVALID` and `TREADY` are asserted.

This handshake allows downstream back-pressure to propagate to the source. If the DMA temporarily cannot accept another word, the Q-Crate stream engine holds the pending transfer rather than discarding the sample.

The stream engine currently supports two sources. A deterministic counter-pattern mode was retained because it provides a simple method of verifying framing and DMA integrity. The second mode selects the integrated DSP output.

```text
                 +------------------+
counter pattern ->|                  |
                  |  stream engine  |----> AXI4-Stream ----> DMA
DSP IQ stream --->|                  |
                 +------------------+
```

The pattern source is useful for determining whether corruption originates in the data transport system rather than in DSP arithmetic. Once the DMA path has been verified independently, the same framing mechanism can carry DSP output.

## DSP integration

The Q-Crate DSP chain is part of the implemented architecture.

The present signal source is synthetic rather than an external ADC. It produces deterministic digital input samples so that the complete acquisition chain can be compared against a bit-accurate software model.

The current processing path is:

```text
synthetic sampled source
        |
        v
digital down-conversion
        |
        v
complex I/Q
        |
        v
FIR decimation by 16
        |
        v
16-bit I + 16-bit Q
        |
        v
32-bit AXI4-Stream word
```

The source operates in the 200 MHz stream domain. Digital down-conversion translates the selected spectral component to complex baseband, after which the FIR stage performs low-pass filtering and decimation.

The packed output word contains a signed 16-bit I value and a signed 16-bit Q value:

```text
31               16 15                0
+------------------+------------------+
|     Q[15:0]      |      I[15:0]     |
+------------------+------------------+
```

The implemented DSP chain has been captured through the KV260 DMA path and compared with its bit-accurate Python reference model. The hardware and model produced matching captured samples for the accepted DSP test configuration.

The synthetic source is therefore not an architectural placeholder for an unfinished DSP subsystem. It is the current digital stimulus at the front of an implemented DSP and acquisition path. A physical ADC can later replace this source while leaving the downstream DDC, filtering, framing, DMA and software interfaces structurally intact.

## Deterministic sequencer and timebase

Q-Crate contains a hardware event sequencer in the 200 MHz timing domain.

A sequence consists of timestamped events stored in dual-clock event memory. Each event describes a required output state at a particular offset from the beginning of the sequence.

Before execution, the event sequence is loaded and validated. During execution, the PL compares the current elapsed time with the event timestamps and updates the sequence outputs at the specified hardware ticks.

A 64-bit counter provides the common hardware timebase. At 200 MHz, each increment represents 5 ns.

The important architectural separation is:

```text
R5 firmware:
    load
    validate
    arm
    start
    abort
    inspect status

PL sequencer:
    count hardware ticks
    fetch events
    compare timestamps
    change outputs
```

The R5 does not wake up every 5 ns to generate a pulse. It establishes and supervises the sequence; the FPGA executes the timing.

The present sequencer supports two logical pulse outputs. In the current KV260 top-level design these signals remain internal to the programmable-logic integration rather than being committed to a final external instrument connector. Their timing engine and control path are nevertheless implemented and verified.

## R5 real-time control layer

R5-0 runs a FreeRTOS-based Q-Crate control service.

Communication between Linux and the R5 uses **OpenAMP/RPMsg**. OpenAMP provides the heterogeneous-processing communication framework, while RPMsg provides message-based communication between the Linux and remote-processor environments.

The operational relationship is:

```text
Linux application
       |
       | RPMsg request
       v
R5-0 FreeRTOS Q-Crate service
       |
       | memory-mapped APB access
       v
PL sequence registers
       |
       v
sequence engine
```

The R5 service directly accesses the Q-Crate register space and owns the sequence upload and lifecycle operations. It verifies sequence information, writes event memory, issues arm/start/abort commands and reads sequencer status.

This design keeps real-time sequence supervision outside the general-purpose Linux scheduler while retaining Linux as the system-management environment.

OpenAMP was initially developed as a vertical integration experiment within Q-Crate, but it is now part of the implemented control architecture used by the reference application.

## Linux, DMA and memory ownership

Linux on the A53 processors owns the bulk acquisition infrastructure.

The AXI DMA writes completed PL frames into DDR memory. Scatter-gather operation allows Linux to construct a chain of descriptors describing multiple destination buffers before the stream begins.

This arrangement separates two kinds of activity:

```text
PL / DMA                       Linux

produce samples  -----------> receive into prepared memory
execute framing               own DMA descriptors
assert TLAST                  manage completed buffers
                              package measurement records
                              send data over Ethernet
```

Linux therefore does not copy each FPGA sample individually as it arrives. The DMA engine performs the high-rate transfer into memory, after which software operates on completed memory regions.

The finite-shot implementation uses coherent DMA buffers and scatter-gather descriptor chains. This provides the basis for repeated acquisition in which buffer ownership must remain explicit while one part of the system fills memory and another part transmits previously completed measurements.

## Network data path

After a DMA capture is complete, the measurement is transferred from the KV260 to the host through Ethernet.

The implemented path is:

```text
PL DSP / stream engine
        |
        v
AXI DMA
        |
        v
DDR capture buffer
        |
        v
A53 Linux data-plane sender
        |
        v
Gigabit Ethernet / UDP
        |
        v
host receiver
        |
        +--> validation
        +--> recording
        +--> replay
        +--> time-domain analysis
        +--> magnitude / phase
        +--> spectrum
```

Q-Crate Data Plane v1 defines the binary UDP representation used for this transfer. The protocol was frozen after end-to-end KV260-to-host acceptance of the finite acquisition path.

The network protocol does not replace the internal AXI4-Stream interface. Instead, the complete acquisition crosses several interfaces according to where the data is located:

```text
DSP             AXI4-Stream
PL -> DDR        AXI DMA
DDR -> Linux     memory / DMA driver
Linux -> host    UDP Data Plane v1
```

Each interface solves a different part of the transport problem.

## Interrupt architecture

The programmable logic provides an interrupt toward the processing system for events that require software attention.

Within `qcrate_core`, event pulses generated in the 200 MHz execution domain are transferred into the control domain and converted into software-visible interrupt state. Interrupt events are stored as sticky status rather than requiring software to observe a single-cycle FPGA pulse directly.

This distinction is important because a one-cycle pulse at 200 MHz lasts only 5 ns, far shorter than software polling intervals. The hardware therefore records the occurrence of the event until software acknowledges it.

The interrupt mechanism complements status registers: status describes the present state of a subsystem, whereas an interrupt can record that a significant event occurred.

## Reset architecture

The two principal clock domains have corresponding active-low reset signals:

```text
pl_clk0    200 MHz stream/timing clock
pl_arstn0  stream/timing reset

pl_clk1    100 MHz control clock
pl_arstn1  control reset
```

The generated block design contains the processor-system reset infrastructure used to derive resets associated with the PL clocks.

Reset behaviour is significant in a heterogeneous design because the processors, AXI infrastructure, DMA and custom logic cannot be treated as a single flip-flop network. Each subsystem must enter a defined state, and reset release must respect the clock domain in which the receiving logic operates.

The AXI DMA spans the control and payload domains internally. Its external reset is associated with the slower control side, while the DMA IP performs the required internal handling for its asynchronous clock configuration.

Within `qcrate_core`, the control and stream reset inputs independently initialise the state belonging to their respective clock domains.

## Current architecture baseline

The principal implemented elements of the Q-Crate KV260 architecture are summarised below.

| Element | Current implementation |
|---|---|
| Hardware platform | AMD Kria KV260 / K26 Zynq UltraScale+ MPSoC |
| PL control clock | 100 MHz |
| PL stream/timing clock | 200 MHz |
| Custom control bus | 32-bit APB |
| Processor-to-control path | AXI SmartConnect and AXI-to-APB bridge |
| Stream transport | 32-bit AXI4-Stream |
| Memory transfer | AXI DMA S2MM with scatter-gather |
| DDR access | PS HP0 path through data SmartConnect |
| Top-level RTL | `qcrate_top.sv` |
| Main custom RTL | `qcrate_core.sv` |
| Register structure | System, stream and sequence APB pages |
| CDC | Explicit command, event and status crossings |
| Timing | 64-bit, 200 MHz hardware timebase |
| Sequencing | Deterministic two-channel event sequencer |
| DSP | Synthetic source, DDC, complex I/Q and decimate-by-16 FIR |
| Real-time processor | R5-0 running FreeRTOS |
| A53/R5 communication | OpenAMP / RPMsg |
| A53 software | Embedded Linux / PetaLinux |
| DMA software | Linux DMAEngine-based capture |
| Network transport | Gigabit Ethernet, UDP Data Plane v1 |
| Host software | Receiver, capture recording, replay and DSP analysis |

This architecture forms the present digital foundation of Q-Crate. The synthetic signal source and internal pulse outputs allow the timing, DSP, control, DMA and networking layers to be developed and measured without yet depending on an analogue front end.

A later physical acquisition interface changes the boundary at which real-world signals enter the system, but it does not fundamentally alter the division between deterministic programmable logic, real-time supervision, Linux data management and host-level analysis.
