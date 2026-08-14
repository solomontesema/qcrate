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

Before inserting the switch, the development PC was observed as:

```text
wlp2s0    192.168.1.104/24   connected through Wi-Fi
enp3s0f1  no carrier         wired Ethernet cable absent
router    192.168.1.254
KV260     192.168.1.93       MAC 00:0a:35:0f:28:c4
```

This is suitable for SSH administration but not for the accepted throughput
baseline: a PC-on-Wi-Fi/KV260-on-Ethernet result includes Wi-Fi behavior.

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

On the PC:

```bash
ip -br link
ip -br address
ip route
ip route get 192.168.1.93
sudo ethtool enp3s0f1
```

The route lookup must report `dev enp3s0f1`, and `ethtool` should report:

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

On the PC, using the accepted wired host address as `HOST_IP`:

```bash
mkdir -p build/network
python3 kv260/linux/network/network_baseline.py \
  --label host \
  --interface enp3s0f1 \
  --peer 192.168.1.93 \
  --output build/network/host.txt
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
  build/network/kv260.txt
```

## TCP throughput

Start a temporary server on the KV260:

```bash
iperf3 -s
```

From the PC, test PC to KV260 and then KV260 to PC. `-O 3` omits the TCP
slow-start interval from the reported steady-state result:

```bash
iperf3 -c 192.168.1.93 -t 30 -O 3 --get-server-output \
  --json >build/network/tcp-host-to-kv260.json

iperf3 -c 192.168.1.93 -R -t 30 -O 3 --get-server-output \
  --json >build/network/tcp-kv260-to-host.json
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
    >"build/network/udp-host-to-kv260-${rate}.json"
done

for rate in 100M 500M 800M 900M; do
  iperf3 -c 192.168.1.93 -R -u -b "$rate" -l 1400 -t 30 -O 3 \
    --get-server-output --json \
    >"build/network/udp-kv260-to-host-${rate}.json"
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

## Packet capture

Capture only Q-Crate-peer UDP traffic on the wired PC interface:

```bash
sudo tcpdump -ni enp3s0f1 \
  'host 192.168.1.93 and udp' \
  -w build/network/qcrate-udp.pcap
```

For a short text view without DNS or service-name lookups:

```bash
sudo tcpdump -ni enp3s0f1 -nn -c 50 \
  'host 192.168.1.93 and udp'
```

Use Wireshark for offline inspection of the `.pcap`. The production Q-Crate
packet header will later receive a dissector or a compact validation script if
ordinary UDP inspection is insufficient.

## Acceptance record

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
