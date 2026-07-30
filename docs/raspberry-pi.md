# Raspberry Pi OS Bookworm and Trixie

Raspberry Pi OS Bookworm (Debian 12) and Trixie (Debian 13) are supported where
the required packages are available. This includes ARM64 and ARM32 on the
Raspberry Pi Zero 2 W. For USB printers, its data port needs a micro-USB OTG
adapter. A powered USB hub often improves stability; the printer should use its
own power supply rather than drawing printer power from the Pi.

Give the Pi a DHCP reservation or static address so clients and administrators
can find it consistently. Wi-Fi power management can cause intermittent
availability; check `iw dev wlan0 get power_save` and configure the OS network
stack appropriately if dropouts correlate with idle periods.

CUPS `usb://` URIs containing a serial number are substantially more stable
than model-only URIs. With multiple identical printers and no unique serials,
port order may change after boot. See [USB printers](usb-printers.md).

The Zero 2 W has limited memory and CPU. Building rastertoescpos can take time,
but it is performed only when the filter and its two expected models are
missing. A powered hub, quality power supply, and adequate free storage are
more important than increasing CUPS concurrency.

Trixie uses Python 3.13 and a newer CUPS package, but retains the CUPS PPD
compiler and development interfaces needed by rastertoescpos. The installer
uses distribution package names available on both Bookworm and Trixie.
