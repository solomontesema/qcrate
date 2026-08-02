# KV260 hardware notes

## APB Verilator test

This lightweight simulation checks the APB fabric and system-register page without Vivado. It does not compile `qcrate_core.sv` yet because later stream, CDC, and IRQ modules are still planned work.

Build and run from the repository root:

```bash
mkdir -p build/verilator/qcrate_apb_tb

CCACHE_DISABLE=1 verilator --binary --timing -Wall \
  --top-module qcrate_apb_tb \
  --Mdir build/verilator/qcrate_apb_tb \
  kv260/hw/tb/qcrate_apb_tb.sv \
  kv260/hw/rtl/qcrate_apb_fabric.sv \
  kv260/hw/rtl/qcrate_sys_regs.sv

./build/verilator/qcrate_apb_tb/Vqcrate_apb_tb
```

Expected result:

```text
PASS: qcrate_apb_tb
```

The test covers system identity reads, scratch write/readback, setup-only write rejection, back-to-back accesses, writes to read-only registers, unmapped system offsets, unmapped APB pages, and stream-page routing through the fabric.

## Stream-register and IRQ Verilator test

This simulation checks the stream-control APB page and sticky interrupt controller in the 100 MHz control domain. It intentionally does not test command/status CDC; that architecture is decided separately.

Build and run from the repository root:

```bash
mkdir -p build/verilator/qcrate_stream_regs_irq_tb

CCACHE_DISABLE=1 verilator --binary --timing -Wall \
  --top-module qcrate_stream_regs_irq_tb \
  --Mdir build/verilator/qcrate_stream_regs_irq_tb \
  kv260/hw/tb/qcrate_stream_regs_irq_tb.sv \
  kv260/hw/rtl/qcrate_stream_regs.sv \
  kv260/hw/rtl/qcrate_irq_ctrl.sv

./build/verilator/qcrate_stream_regs_irq_tb/Vqcrate_stream_regs_irq_tb
```

Expected result:

```text
PASS: qcrate_stream_regs_irq_tb
```

The test covers configuration register reset and readback, `CONTROL` write-one-pulse command bits, `CONTINUOUS` storage, status bit layout, sticky IRQ events, IRQ enable masking, `IRQ_CLEAR` write-one-to-clear behavior, event-vs-clear precedence, writes to read-only registers, and unmapped stream-page errors.

## CDC Verilator test

The CDC RTL uses `qcrate_cdc_single` as the local wrapper for single-bit synchronizers. In Vivado builds this wrapper instantiates `xpm_cdc_single`; in Verilator it uses a small two-flop fallback because Verilator does not provide the Xilinx XPM library.

The multi-bit command and status buses are not synchronized bit-by-bit. They use toggle handshakes: the source holds the multi-bit bus stable, the toggle crosses through `qcrate_cdc_single`, and the destination copies the held bus after observing the synchronized toggle.

Build and run from the repository root:

```bash
mkdir -p build/verilator/qcrate_cdc_tb

CCACHE_DISABLE=1 verilator --binary --timing -Wall \
  --top-module qcrate_cdc_tb \
  --Mdir build/verilator/qcrate_cdc_tb \
  kv260/hw/tb/qcrate_cdc_tb.sv \
  kv260/hw/rtl/qcrate_cdc_single.sv \
  kv260/hw/rtl/qcrate_event_cdc.sv \
  kv260/hw/rtl/qcrate_command_cdc.sv \
  kv260/hw/rtl/qcrate_status_cdc.sv

./build/verilator/qcrate_cdc_tb/Vqcrate_cdc_tb
```

Expected result:

```text
PASS: qcrate_cdc_tb
```

The test covers sparse event pulse crossing, command mailbox delivery, command busy rejection, and coherent status snapshot refresh.

## Stream-engine Verilator test

This simulation checks the first AXI4-Stream pattern source used for DMA bring-up. The current MVP stream word is:

```text
TDATA[31:16] = frame ID
TDATA[15:0]  = sample index
TKEEP        = 4'b1111
TLAST        = final sample of a frame
```

Build and run from the repository root:

```bash
mkdir -p build/verilator/qcrate_stream_engine_tb

CCACHE_DISABLE=1 verilator --binary --timing -Wall \
  --top-module qcrate_stream_engine_tb \
  --Mdir build/verilator/qcrate_stream_engine_tb \
  kv260/hw/tb/qcrate_stream_engine_tb.sv \
  kv260/hw/rtl/qcrate_stream_engine.sv

./build/verilator/qcrate_stream_engine_tb/Vqcrate_stream_engine_tb
```

Expected result:

```text
PASS: qcrate_stream_engine_tb
```

The test covers one-word frames, multi-word finite frames, continuous mode, `TLAST`, `TKEEP`, held AXI4-Stream outputs during backpressure, graceful abort after the currently presented word is accepted, soft reset, and invalid start configuration.

For this first implementation, `STREAM_MODE` is reserved. `FRAME_LENGTH=0` is invalid. In finite mode, `FRAME_COUNT=0` is also invalid. In continuous mode, `FRAME_COUNT` is ignored.

A lightweight Vivado parser check for the same area is:

```bash
source /tools/Xilinx/Vivado/2024.2/settings64.sh

xvlog --sv \
  kv260/hw/rtl/qcrate_stream_engine.sv \
  kv260/hw/tb/qcrate_stream_engine_tb.sv \
  kv260/hw/rtl/qcrate_core.sv
```

Expected result: each file is analyzed with no `ERROR` messages. This is not synthesis and does not prove timing closure.

## ILA workflow

Your usual `.xci`-based ILA flow is valid and common for Vivado projects, but it is not the most reproducible starting point for this repository. A more git-friendly approach is:

1. Keep the handwritten RTL clean and leave `qcrate_debug` optional behind ``QCRATE_ENABLE_ILA``.
2. Generate debug IP from Tcl during the Vivado project build, or instantiate debug cores only in a small wrapper file that is excluded unless debug is enabled.
3. Commit the Tcl or minimal `.xci` inputs, not generated `.runs`, `.cache`, `.gen`, or synthesized IP output.
4. Use ILA for hardware questions that simulation cannot answer: DMA `TREADY` stalls, `TLAST` position at the DMA input, IRQ level behavior, reset sequencing, and APB writes from Linux.

For learning, the `.xci` wizard method is useful because you can see the core options visually. For reproducible team work, a Tcl-created ILA or a committed `.xci` with all generated products rebuilt locally is usually cleaner. We can leave the first ILA implementation for you and later review the Tcl or `.xci` before it enters the public repo.
