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
