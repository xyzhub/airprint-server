# Troubleshooting

Start with:

```sh
sudo airprint-server diagnose --printer QUEUE
sudo airprint-server diagnose --printer QUEUE --logs
```

Each failed check includes a focused investigation action. Useful independent
checks are:

```sh
cupsd -t
lpstat -t
lpinfo -m
lpinfo -v
avahi-browse -rt _ipp._tcp
journalctl -u cups -u avahi-daemon --no-pager -n 50
```

If a queue prints locally but is absent on iOS, verify it is shared, CUPS and
Avahi are active, and multicast DNS (UDP 5353) crosses neither a guest Wi-Fi
isolation boundary nor a routed VLAN without an mDNS reflector. Avoid custom
Avahi service files until standard CUPS advertisement is proven insufficient;
duplicates make diagnosis harder.

If a socket printer is unreachable, test `nc -vz HOST 9100`. A failed ping
alone is not evidence of a print failure. If jobs complete but paper stays
blank, confirm the selected PPD/filter and inspect CUPS' recent error log.

For USB absence or instability, follow [USB printers](usb-printers.md).

An image printed from an iPhone validates discovery, AirPrint submission,
CUPS conversion, the selected driver, transport, and printer. A web page that
lays out poorly only in Safari print may instead need application print CSS;
that is outside this server configuration.

