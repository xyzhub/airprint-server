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

