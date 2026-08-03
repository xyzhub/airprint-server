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
raw TCP/JetDirect printer. Answer yes and accept port 9100 for the first queue.
Additional queues on the same Pi must use distinct ports such as 9101 and
9102; a single IP address cannot route more than one printer through port
9100.

For an existing managed queue, the equivalent explicit command is:

```sh
sudo airprint-server expose-raw BIXOLON-SRP-E300
```

The command selects the first unused port starting at 9100. A specific port
can be requested with `--port 9101`. Disable exposure without deleting the
CUPS queue with:

```sh
sudo airprint-server unexpose-raw BIXOLON-SRP-E300
```

On a client, create a Standard TCP/IP, AppSocket, or HP JetDirect printer using
the Raspberry Pi's LAN address and the assigned port. The client must use the
correct printer driver. Data received through this listener is submitted to
CUPS in raw mode and sent byte-for-byte to the physical printer; the server's
PPD and raster filter are intentionally bypassed to avoid rendering an already
rendered job a second time.

Each TCP connection is one print job. The managed service runs as the
unprivileged `lp` account, accepts at most four simultaneous jobs, limits each
job to 32 MiB, and finishes an inactive connection after 60 seconds. It listens
on IPv4 interfaces but accepts only loopback, link-local, and private IPv4
source addresses. It provides neither authentication nor encryption. Never
forward these ports from the internet; restrict them to a trusted LAN or
printer VLAN with the router/firewall as an additional boundary.

Inspect configured mappings and the listener:

```sh
sudo airprint-server list-printers
systemctl status airprint-server-raw --no-pager
sudo ss -ltnp | grep -E ':910[0-9]'
```
