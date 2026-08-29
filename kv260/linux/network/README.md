# Q-Crate wired network baseline

## Purpose

This milestone establishes the real host-to-KV260 transport environment before
the Q-Crate UDP packet format and streamer are designed. It is not a network
optimization exercise. The baseline answers four practical questions:

1. Which physical interface and route carry Q-Crate traffic?
2. Is the Ethernet link actually 1 Gbit/s, full duplex, with MTU 1500?
3. What TCP and UDP rates are repeatable in both directions?
4. At what requested UDP rate do loss or jitter become material?

Without this evidence, later packet loss could be incorrectly attributed to
DMA, Linux scheduling, packetization, the FPGA design, or the receiver.

## Recommended topology

Use the Gigabit switch as the common Q-Crate Layer-2 network:

```text
home router LAN port
        |
        | switch uplink: DHCP and Internet access only
        v
Gigabit Ethernet switch
        |-- development PC, wired Ethernet
        |-- KV260
        |-- PYNQ-Z2, later
        `-- management devices, later if appropriate
```

Traffic between the PC and KV260 remains inside the switch; it does not pass
through the router data path. The router supplies DHCP, DNS, and Internet
access. Existing devices such as the TV may remain connected directly to the
router.

For the initial baseline, leave the smart-switch features at their ordinary
defaults:

- one untagged LAN, normally VLAN 1;
- no rate limiting or storm-control experiment;
- no port isolation;
- no QoS tuning;
- MTU 1500, not jumbo frames.

VLANs, QoS, and jumbo frames are valid later experiments, but enabling them now
would add variables before the basic path is measured.

## Current observed state

The accepted host-side physical path now uses a USB 3 Gigabit Ethernet adapter:

```text
host wired interface  : enx00e04c681d13
host wired address    : 192.168.1.92/24 (current DHCP address)
adapter/driver        : RTL8153 / r8152
negotiated link       : 1000 Mb/s, full duplex
MTU                   : 1500
KV260 address         : 192.168.1.93 (current DHCP address)
```

The host route to `192.168.1.93` selects `enx00e04c681d13` with the lower route
metric. The previous onboard `enp3s0f1` measurements negotiated at only
10 Mb/s and are not valid evidence for Data Plane v1.

## Address ownership

Prefer DHCP reservations in the home router for the development network. A
reservation keeps each address stable while preserving one source of truth for
gateway, DNS, and subnet configuration. Reserve addresses using the interface
MAC addresses after the wired PC and KV260 appear in the router's client list.

Do not hard-code addresses in the PetaLinux image merely for this home setup.
A future isolated lab network can instead use a tracked `systemd-networkd`
configuration or a dedicated DHCP server.

Record the accepted values here after reservation:

```text
subnet       : 192.168.1.0/24
router       : 192.168.1.254
host wired   : TBD
KV260        : 192.168.1.93 (current DHCP address; reserve or replace)
PYNQ-Z2      : TBD
```

## Required tools

The tracked PetaLinux image package list includes:

```text
iperf3 ethtool tcpdump
```

They will appear on the KV260 after the next PetaLinux image build and SD-card
deployment. The development PC currently has `ping` and `tcpdump`, but needs
`iperf3` and `ethtool`:

```bash
sudo apt update
sudo apt install iperf3 ethtool
```

The package installer may ask whether to start an `iperf3` daemon. Select no;
Q-Crate starts temporary foreground servers explicitly during tests.

## Connect and verify the physical path

1. Connect one switch port to a home-router LAN port.
2. Connect the KV260 and `enp3s0f1` on the PC to switch ports.
3. Wait for link negotiation and DHCP.
4. Confirm that traffic to the KV260 uses wired Ethernet.

On the PC, substitute the current wired interface if the USB adapter receives a
different predictable name:

```bash
HOST_IF=enx00e04c681d13
KV260_IP=192.168.1.93
ip -br link
ip -br address
ip route
ip route get "$KV260_IP"
sudo ethtool "$HOST_IF"
```

The route lookup must report the selected wired interface, and `ethtool` should
report:

```text
Speed: 1000Mb/s
Duplex: Full
Link detected: yes
```

If Wi-Fi and wired Ethernet are both attached to `192.168.1.0/24`, route
metrics decide which interface is used. During acceptance, disconnect Wi-Fi if
the route still selects it:

```bash
nmcli device disconnect wlp2s0
```

Restore it afterward if needed:

```bash
nmcli device connect wlp2s0
```

Do not proceed with throughput tests when link speed is 100 Mb/s. Check the
cable, connectors, and switch-port negotiation first.

## Capture compact state reports

The supplied standard-library script changes no network configuration. Run it
on both endpoints and retain the reports under the ignored `build/` directory.

Create a new timestamped directory for every acceptance run. Do not overwrite or
mix results from a previous physical interface:

```bash
RUN="build/network/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$RUN"
python3 kv260/linux/network/network_baseline.py \
  --label host \
  --interface "$HOST_IF" \
  --peer 192.168.1.93 \
  --output "$RUN/host.txt"
```

Copy the script to the board until it is packaged in a later image:

```bash
scp kv260/linux/network/network_baseline.py \
  petalinux@192.168.1.93:~/network_baseline.py
```

On the KV260, first discover its Ethernet interface name:

```bash
ip -br link
```

Then substitute that name and the accepted wired PC address:

```bash
python3 ~/network_baseline.py \
  --label kv260 \
  --interface <KV260_INTERFACE> \
  --peer <HOST_IP> \
  --output /tmp/kv260-network.txt
```

Return the target report to the repository build directory:

```bash
scp petalinux@192.168.1.93:/tmp/kv260-network.txt \
  "$RUN/kv260.txt"
```

## Automated throughput acceptance

Start a temporary `iperf3` server on the KV260:

```bash
iperf3 -s
```

On the host, reuse the timestamped `$RUN` directory containing the endpoint
snapshots. The runner permits those text reports but refuses to overwrite any
existing iperf JSON or summary, so stale measurements cannot be mixed:

```bash
# Keep RUN set to the directory created for the endpoint snapshots above.
test -n "$RUN"
python3 kv260/linux/network/run_iperf.py \
  --server 192.168.1.93 \
  --output-dir "$RUN"
cat "$RUN/iperf-summary.md"
```

This executes the TCP and UDP commands described below in both directions and
validates every JSON document before publishing it. Review the complete command
set without generating traffic with:

```bash
python3 kv260/linux/network/run_iperf.py \
  --server 192.168.1.93 \
  --output-dir build/network/not-created \
  --dry-run
```

Stop the KV260 server with `Ctrl-C` after the sweep.

## TCP throughput

The automated runner uses these underlying commands. For a focused diagnostic,
start a temporary server on the KV260:

```bash
iperf3 -s
```

From the PC, test PC to KV260 and then KV260 to PC. `-O 3` omits the TCP
slow-start interval from the reported steady-state result:

```bash
iperf3 -c 192.168.1.93 -t 30 -O 3 --get-server-output \
  --json >"$RUN/tcp-host-to-kv260.json"

iperf3 -c 192.168.1.93 -R -t 30 -O 3 --get-server-output \
  --json >"$RUN/tcp-kv260-to-host.json"
```

Stop the server with `Ctrl-C` after both runs. A healthy dedicated Gigabit path
often approaches roughly 900 Mbit/s or more for one TCP stream, but Q-Crate
records the measured result rather than using that estimate as a pass criterion.
Large directional asymmetry, retransmissions, or a result near 100 Mbit/s must
be investigated before UDP work.

## UDP throughput and loss boundary

Restart `iperf3 -s` on the KV260. Test increasing offered rates with a
1400-byte payload, which remains below the normal Ethernet MTU after headers:

```bash
for rate in 100M 500M 800M 900M; do
  iperf3 -c 192.168.1.93 -u -b "$rate" -l 1400 -t 30 -O 3 \
    --get-server-output --json \
    >"$RUN/udp-host-to-kv260-${rate}.json"
done

for rate in 100M 500M 800M 900M; do
  iperf3 -c 192.168.1.93 -R -u -b "$rate" -l 1400 -t 30 -O 3 \
    --get-server-output --json \
    >"$RUN/udp-kv260-to-host-${rate}.json"
done
```

Stop increasing the offered rate if loss becomes large or the board becomes
unresponsive. The accepted baseline records, in each direction:

- offered and received Mbit/s;
- lost datagrams and loss percentage;
- jitter;
- CPU load if reported;
- first rate at which loss becomes material.

Zero loss at a modest rate is more important to the first Q-Crate packetizer
than reaching wire speed. The later streamer chooses an operating point below
the measured loss boundary and implements explicit sequence/loss detection.

Render the machine-generated JSON results as one compact Markdown table:

```bash
python3 kv260/linux/network/iperf_summary.py \
  "$RUN"/*.json \
  --output "$RUN/iperf-summary.md"
cat "$RUN/iperf-summary.md"
```

The summary reports receiver throughput. Iperf's UDP aggregate byte/rate fields
count offered datagrams even in reverse mode, while its loss fields report the
receiver observation. The script therefore derives received rate as
`transmitted_rate * (1 - loss_fraction)` in both directions.

## Packet capture

Capture only Q-Crate-peer UDP traffic on the wired PC interface:

```bash
sudo tcpdump -ni "$HOST_IF" \
  'host 192.168.1.93 and udp' \
  -w build/network/qcrate-udp.pcap
```

For a short text view without DNS or service-name lookups:

```bash
sudo tcpdump -ni "$HOST_IF" -nn -c 50 \
  'host 192.168.1.93 and udp'
```

Use Wireshark for offline inspection of the `.pcap`. The production Q-Crate
packet header will later receive a dissector or a compact validation script if
ordinary UDP inspection is insufficient.

## Acceptance record

The accepted 2026-08-29 Gigabit measurement, including the default-buffer loss
near Q-Crate's intended stream rate, is recorded in
[BASELINE.md](BASELINE.md). Use the following template when replacing it with a
new network, host adapter, kernel, or receiver implementation.

This milestone is complete when all entries are filled from wired tests:

```text
test date UTC             : TBD
switch/cabling topology   : TBD
host interface/IP/MAC     : TBD
KV260 interface/IP/MAC    : TBD
negotiated link           : TBD (expected 1000 Mb/s, full duplex)
MTU                       : TBD (baseline expected 1500)
average ping / loss       : TBD
TCP host -> KV260         : TBD
TCP KV260 -> host         : TBD
UDP loss-free host -> KV260: TBD
UDP loss-free KV260 -> host: TBD
first material-loss point : TBD
```

Keep raw reports and iperf JSON in `build/network/`. Commit only a concise
summary once the addresses and measurements are stable; raw captures may expose
home-network details and can become large.
