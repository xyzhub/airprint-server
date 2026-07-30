# airprint-server

A lightweight AirPrint server for USB and network printers, with built-in
profiles for ESC/POS thermal receipt printers.

`airprint-server` configures CUPS as the print spooler and Avahi as the
Bonjour/DNS-SD advertiser. Printer profiles supply model-specific driver and
media defaults; connection settings independently select USB, raw socket, IPP,
IPPS, LPD, or another CUPS URI. This separation also supports installed vendor
drivers and custom PPD files.

## Support and limitations

The supported hosts are Debian 12 Bookworm, Debian 13 Trixie, Raspberry Pi OS
Bookworm, and Raspberry Pi OS Trixie on ARM64, ARM32 where Debian packages are
available, and x86_64. The known working hardware path is:

- Printer: SwissPOS SPST80C / T80C
- Connection: `socket://192.168.1.123:9100`
- Profile: `swisspos-t80c`
- Driver: `drv:///escpos.drv/gp80160.ppd`
- Page size: `w226h842`
- Resolution: 203 dpi

Generic ESC/POS profiles are starting points, not a claim that every ESC/POS
printer is compatible. Cutter behavior, printable width, USB enumeration, and
vendor firmware vary. Installer compatibility checks cover Bookworm and
Trixie, but physical printer validation remains necessary.

## Installation

On a fresh supported machine:

```sh
git clone <repository-url>
cd airprint-server
sudo ./install.sh
```

The installer creates an isolated runtime under `/opt/airprint-server`, exposes
`/usr/local/bin/airprint-server`, installs only the required Debian packages,
enables CUPS and Avahi, enables printer sharing without enabling remote CUPS
administration, and builds
[rastertoescpos](https://github.com/chunlinyao/rastertoescpos) only if its filter
and expected PPD models are absent. Its upstream Makefiles use `cups-config`,
the CUPS image library, and `ppdc`; the build dependencies are therefore
`build-essential`, `libcups2-dev`, and `cups-ppdc` plus Git.

Skip ESC/POS build dependencies if they are not needed:

```sh
sudo ./install.sh --without-escpos
```

Repeated installation checks packages and driver availability before acting.
It does not recreate queues or reset their options.

## Add printers

Raw TCP network ESC/POS:

```sh
sudo airprint-server add-printer \
  --name SwissPOS \
  --description "SwissPOS AirPrint ESC-POS" \
  --profile swisspos-t80c \
  --connection socket \
  --host 192.168.1.123 \
  --port 9100
```

USB with interactive selection from stable CUPS `usb://` URIs:

```sh
sudo airprint-server add-printer \
  --name CounterPrinter \
  --profile escpos-generic-80mm \
  --connection usb
```

USB non-interactively:

```sh
sudo airprint-server add-printer \
  --name CounterPrinter \
  --profile escpos-generic-80mm \
  --device-uri 'usb://XPrinter/XP-80C?serial=123456'
```

Installed vendor driver:

```sh
sudo airprint-server add-printer \
  --name OfficePrinter \
  --profile generic-driverless \
  --device-uri 'usb://Vendor/Model?serial=1234' \
  --driver 'drv:///vendor/model.ppd'
```

Custom PPD:

```sh
sudo airprint-server add-printer \
  --name OfficePrinter \
  --profile generic-driverless \
  --device-uri 'usb://Vendor/Model?serial=1234' \
  --ppd /path/to/vendor.ppd
```

Driverless IPP:

```sh
sudo airprint-server add-printer \
  --name OfficeIPP \
  --profile generic-driverless \
  --connection ipp \
  --device-uri 'ipp://printer.local/ipp/print'
```

Use `--dry-run` to see system commands and `--yes` for non-interactive
confirmation. Queue names, ports, hosts, URIs, PPD paths, profile fields, and
CUPS options are validated before commands run. Commands never use a shell to
interpolate user data.

## Discovery, status, and diagnostics

```sh
airprint-server list-profiles
airprint-server discover-usb
airprint-server discover
airprint-server list-printers
airprint-server status
sudo airprint-server diagnose --printer SwissPOS
sudo airprint-server diagnose --printer SwissPOS --logs
```

Diagnostics check the operating system, services, CUPS configuration, queue
state, sharing, driver, advertisement, and connection-specific health. They use
a TCP connection test for socket printers rather than assuming ICMP ping works.
Relevant logs are opt-in and limited to recent CUPS, Avahi, and `ipp-usb`
journal entries.

Some IPP-over-USB printers are claimed by `ipp-usb` and may not appear through
the CUPS USB backend. The tool reports this situation and never disables the
service implicitly. Explicitly opt in:

```sh
sudo airprint-server add-printer ... --disable-ipp-usb
```

The previous service state is recorded and restored during uninstall.

## Test printing

```sh
sudo airprint-server test --printer SwissPOS
```

The generated job passes through CUPS and contains plain text, width lines,
printer/profile/time details, and a checkerboard raster calibration pattern.
For a cutter-capable ESC/POS profile, the separate raw hardware-action test
requires confirmation or `--yes`:

```sh
sudo airprint-server test --printer SwissPOS --test-cutter
```

No cutter or cash-drawer command is sent automatically. Printing the image
successfully validates the AirPrint, CUPS, driver, and printer pipeline. Safari
HTML layout problems may instead be caused by the web application's print CSS
and are outside this server's scope.

## Profiles and state

Built-in YAML profiles ship inside the Python package. Local files in
`/etc/airprint-server/profiles.d/` override matching IDs and are never
overwritten. See [printer profile documentation](docs/printer-profiles.md).

- Primary configuration: `/etc/airprint-server/config.yaml`
- Local profiles: `/etc/airprint-server/profiles.d/`
- Managed ownership/state: `/var/lib/airprint-server/state.yaml`
- Log name: `airprint-server`

Only queues in managed state may be updated, tested, or removed. Existing
queues can be recorded without modification:

```sh
sudo airprint-server adopt-printer ExistingQueue
```

## Remove a printer or uninstall

```sh
sudo airprint-server remove-printer SwissPOS
sudo ./uninstall.sh
# or
sudo airprint-server uninstall
```

Uninstall prompts separately before managed queues, configuration, state, or a
project-managed rastertoescpos installation is removed. It leaves CUPS, Avahi,
unrelated queues, vendor drivers, user PPDs, and independently installed
rastertoescpos untouched. Use `--keep-queues`, `--keep-config`, `--keep-state`,
or `--keep-escpos` as needed.

## Development checks

No CUPS daemon or root access is needed:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest
ruff check .
mypy
shellcheck install.sh uninstall.sh scripts/*.sh
```

## Security considerations

CUPS administration stays local by default. Printer sharing exposes print
queues on the local network, so use a trusted LAN and host firewall rules.
Raw port 9100 generally provides neither authentication nor encryption; use
IPP/IPPS where the printer supports it. Custom PPDs and drivers execute in the
printing pipeline and should come only from trusted vendors. State writes are
atomic and system commands use argument arrays, but a print server still
processes untrusted documents and should receive normal Debian security
updates.

More detail: [Raspberry Pi](docs/raspberry-pi.md),
[network printers](docs/network-printers.md), [USB printers](docs/usb-printers.md),
and [troubleshooting](docs/troubleshooting.md).
