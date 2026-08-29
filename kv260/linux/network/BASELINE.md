# Accepted KV260 wired-network baseline

## Scope

This record closes Q-Crate DP-0 by measuring the physical route and the default
Linux network stacks before defining the Data Plane v1 UDP protocol. It records
capacity and loss behavior; it does not claim that the default UDP stack is
lossless at the intended continuous DSP rate.

Test date: 2026-08-29 UTC

## Topology

```text
home router
    |
Gigabit Ethernet switch
    |-- development host: USB 3 RTL8153 adapter
    `-- KV260: ZynqMP GEM
```

| Property | Development host | KV260 |
|---|---|---|
| Interface | `enx00e04c681d13` | `end0` |
| Address during test | `192.168.1.92/24` | `192.168.1.93/24` |
| Driver | `r8152` | `macb` |
| Link | 1000 Mb/s, full duplex | 1000 Mb/s, full duplex |
| MTU | 1500 bytes | 1500 bytes |
| Kernel | Ubuntu `6.8.0-52-generic` | Xilinx `6.6.40` |

Routes in both directions selected the wired interfaces. Ten ICMP requests in
each direction completed without loss. Average round-trip time was 0.378 ms
from the host and 0.476 ms from the KV260. The pre-test Ethernet snapshots
reported no CRC, alignment, overrun, or queue-drop errors.

## Throughput

Measurements used iperf 3.9, 30-second measurement intervals after a 3-second
omission, and 1400-byte UDP datagrams. TCP reached 941.342 Mb/s from host to
KV260 and 941.187 Mb/s from KV260 to host. The latter reported 212 TCP
retransmissions; this did not materially reduce delivered throughput but
remains useful comparison evidence for later stress tests.

| UDP direction | Offered | Received | Loss | Jitter |
|---|---:|---:|---:|---:|
| Host to KV260 | 100 Mb/s | 100.000 Mb/s | 0.000% | 0.011 ms |
| Host to KV260 | 500 Mb/s | 492.541 Mb/s | 1.491% | 0.008 ms |
| Host to KV260 | 800 Mb/s | 556.799 Mb/s | 30.400% | 0.028 ms |
| Host to KV260 | 900 Mb/s | 342.489 Mb/s | 61.946% | 0.047 ms |
| KV260 to host | 100 Mb/s | 100.002 Mb/s | 0.000% | 0.014 ms |
| KV260 to host | 300 Mb/s | 299.799 Mb/s | 0.069% | 0.008 ms |
| KV260 to host | 400 Mb/s | 399.600 Mb/s | 0.102% | 0.018 ms |
| KV260 to host | 420 Mb/s | 419.554 Mb/s | 0.108% | 0.016 ms |
| KV260 to host | 450 Mb/s | 449.535 Mb/s | 0.105% | 0.008 ms |
| KV260 to host | 500 Mb/s | 498.023 Mb/s | 0.397% | 0.012 ms |
| KV260 to host | 800 Mb/s | 798.965 Mb/s | 0.132% | 0.012 ms |
| KV260 to host | 900 Mb/s | 895.679 Mb/s | 0.480% | 0.028 ms |

Both endpoints used the default 212,992-byte maximum send and receive socket
buffers. Physical-interface counters were clean in the endpoint snapshot, so
the UDP loss is treated as host/software receive pressure unless later packet
captures or counter deltas demonstrate otherwise.

## Data-plane consequence

The current 12.5 MS/s, 32-bit IQ stream carries 400 Mb/s of sample payload.
With a 64-byte Q-Crate header inside each 1400-byte UDP datagram, 1336 bytes
remain for samples and the required UDP offered rate is approximately 419 Mb/s.
The measured link has sufficient bandwidth, but its default socket settings
lost about 0.108% at the corresponding 420 Mb/s iperf point.

Data Plane v1 therefore requires packet sequence and frame/sample continuity
checks. Finite triggered shots initially transmit below the accepted loss-free
point when exact delivery is required. Before claiming continuous operation,
the receiver must request a deliberate socket buffer, document any required
`net.core.rmem_max` setting, and repeat sustained loss and CPU measurements.
UDP loss is never silently interpreted as valid sample data.

Raw endpoint reports and iperf JSON remain under the ignored
`build/network/20260829T112144Z/` directory. This tracked file is the concise,
public acceptance record.
