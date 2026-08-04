# Network printers

Connections and printer profiles are independent. A raw thermal printer often
uses TCP 9100:

```sh
sudo airprint-server add-printer \
  --name Receipt \
  --profile escpos-generic-80mm \
  --connection socket --host printer.local --port 9100
```

`--disable-snmp` generates a `socket://host:port/?snmp=false` URI for devices
whose SNMP behavior delays CUPS. The CLI validates host and port and
diagnostics attempt DNS resolution and a short TCP connection. Failure to
answer ICMP ping is not treated as failure.

IPP, IPPS, and LPD require a complete CUPS device URI. IPP/IPPS without an
explicit driver use CUPS' `everywhere` model:

```sh
sudo airprint-server add-printer --name Laser \
  --profile generic-driverless --connection ipps \
  --device-uri 'ipps://printer.local/ipp/print'
```

Raw socket printing is normally unencrypted and unauthenticated. Restrict it
to a trusted printer VLAN/LAN, and prefer IPPS when supported.

## Expose a USB printer as a port-9100 network printer

The setup wizard asks whether each managed queue should also be reachable as a
raw TCP/JetDirect printer. The recommended mode assigns each queue a dedicated
private virtual IPv4 address, allowing all printers to use standard port 9100.
For example, one Pi can expose `192.168.1.240:9100` for receipts and
`192.168.1.241:9100` for labels.

The setup wizard automatically scans the Pi's connected private LAN and proposes
an address for each printer. Press Enter to accept the proposal or type another
address. Reserve the selected addresses or remove them from the router's DHCP
pool. Setup verifies the connected interface and uses ARP duplicate-address
detection, but that cannot prevent a future DHCP lease collision if the
addresses are left in the pool. The aliases are restored automatically at boot
and removed when their exposure is disabled.

Each dedicated endpoint is also advertised as an AppSocket printer using the
standard `_pdl-datastream._tcp` DNS-SD service. The service uses the queue's
friendly display name and a matching mDNS hostname. For example:

```text
BIXOLON SRP-E300
bixolon-srp-e300-printer.local -> 192.168.1.240:9100
```

Bonjour-aware printer dialogs can discover the friendly name automatically.
The `.local` hostname can also be entered instead of the numeric IP on clients
that support mDNS. These names are multicast-DNS records, not separate DHCP
leases: a router may still show all virtual addresses under the Raspberry Pi's
single Wi-Fi or Ethernet hardware address.

For an existing managed queue, the equivalent explicit command is:

```sh
sudo airprint-server expose-raw BIXOLON-SRP-E300 --address 192.168.1.240
```

The interface is inferred from the Pi's connected subnet. The fallback command
without `--address` exposes the queue on the Pi's primary address and selects
the first unused port starting at 9100; a specific fallback port can be
requested with `--port 9101`. Disable exposure without deleting the CUPS queue
with:

```sh
sudo airprint-server unexpose-raw BIXOLON-SRP-E300
```

On a client, create a Standard TCP/IP, AppSocket, or HP JetDirect printer using
the queue's virtual IP address and port 9100. The client must use the correct
printer driver. Data received through this listener is submitted to
CUPS in raw mode and sent byte-for-byte to the physical printer; the server's
PPD and raster filter are intentionally bypassed to avoid rendering an already
rendered job a second time.

Each TCP connection is one print job. The managed service runs as the
unprivileged `lp` account, accepts at most four simultaneous jobs, limits each
job to 32 MiB, and finishes an inactive connection after 60 seconds. It listens
only on configured IPv4 endpoints and accepts only loopback, link-local, and
private IPv4 source addresses. It provides neither authentication nor
encryption. Never
forward these ports from the internet; restrict them to a trusted LAN or
printer VLAN with the router/firewall as an additional boundary.

Inspect configured mappings and the listener:

```sh
sudo airprint-server list-printers
systemctl status airprint-server-raw --no-pager
systemctl status airprint-server-addresses --no-pager
ip -4 address show
sudo ss -ltnp | grep ':9100'
avahi-browse -rt _pdl-datastream._tcp
avahi-resolve-host-name bixolon-srp-e300-printer.local
```
