# airprint-server

A lightweight AirPrint server for USB and network printers, with built-in
profiles for ESC/POS thermal receipt printers.

`airprint-server` configures CUPS as the print spooler and Avahi as the
Bonjour/DNS-SD advertiser. Printer profiles supply model-specific driver and
media defaults; connection settings independently select USB, raw socket, IPP,
IPPS, LPD, or another CUPS URI. This separation also supports installed vendor
drivers and custom PPD files. Managed queues can optionally be exposed back to
the trusted LAN as raw TCP/JetDirect printers, allowing a USB printer to appear
at the Raspberry Pi's address on port 9100.

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

The bundled `bixolon-srp-e300` profile supports the BIXOLON SRP-E300 through
USB or raw socket printing. It uses the model's 180 dpi resolution and limits
the raster to its 72 mm printable area on 79.5 mm roll paper. It explicitly
applies `escCutter=1`, which makes rastertoescpos cut once after the complete
CUPS job rather than after each page. This profile remains labelled
`unverified` pending physical validation.

For the official BIXOLON Linux CUPS driver, run:

```sh
sudo airprint-server install-bixolon-driver
```

The command downloads v1.5.9 directly from BIXOLON after license confirmation,
verifies both the vendor ZIP and its inner driver archive against pinned
SHA-256 checksums, and never runs BIXOLON's legacy setup script. It selects
ARM64 or ARM32 automatically, installs only the SRP-E300 PPD and filter, and
offers to migrate managed SRP-E300 queues. A local archive path can still be
passed for offline installation. The official driver uses an 80 mm logical
media page mapped to the model's centered 72 mm print head. This avoids
stacking application margins on top of a second software-created inset while
retaining 180 dpi output, dithering, fit scaling, and one cut per job.

XPrinter POS-58, POS-76, and POS-80 printers can use XPrinter's current
ARM-capable v3.13.11 driver:

```sh
sudo airprint-server install-xprinter-driver
```

The command downloads the collection from XPrinter after license confirmation,
checks the RAR and inner Debian package against pinned SHA-256 values, and
installs only the three PPDs and the two filters required by them. It never
executes the vendor package's maintainer scripts. The setup wizard performs
this installation automatically when an XPrinter profile needs it.

Generic ESC/POS profiles are starting points, not a claim that every ESC/POS
printer is compatible. Cutter behavior, printable width, USB enumeration, and
vendor firmware vary. Installer compatibility checks cover Bookworm and
Trixie, but physical printer validation remains necessary.

Proprietary vendor driver archives are downloaded from the vendor when needed
or can be placed in the local [`drivers/`](drivers/) drop-in directory for
offline use. They are ignored by Git and are not redistributed by this project;
checked-in manifests record their expected source, version, checksums, and
required model-specific files.

## Installation

On a fresh supported machine:

```sh
git clone https://github.com/xyzhub/airprint-server.git
cd airprint-server
sudo ./install.sh
```

Interactive installations show an animated progress bar tied to actual setup
phases. The percentage advances only when a phase completes; the spinner keeps
moving while package installation or driver compilation is running. When
output is redirected, during a dry run, or with verbose logging enabled, the
same progress is emitted as stable one-line phase messages instead.

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

### Interactive setup wizard

When `sudo ./install.sh` runs in a terminal, it automatically starts a guided
printer setup after installing the system components. The wizard:

1. Detects stable CUPS `usb://` printers and IPP/IPPS devices exposed by
   `ipp-usb`.
2. Offers raw TCP socket, manual IPP/IPPS, LPD, and custom URI connections.
3. Suggests an appropriate profile from the detected make and model.
4. Downloads and verifies the official BIXOLON or XPrinter driver after license
   confirmation when a selected profile needs it; otherwise uses the profile
   driver, suggests matching installed CUPS models, or asks for a vendor PPD.
5. Validates the queue name and AirPrint display name, then optionally exposes
   the queue as a raw TCP/JetDirect printer. Port 9100 is suggested first and
   subsequent printers use 9101, 9102, and so on.
6. When managed queues already exist, offers a separate screen to enable,
   change, or disable their standard Ethernet-printer access without recreating
   the CUPS queue or typing an `expose-raw` command.
7. Shows the normal queue confirmation and offers to configure additional
   printers.

Raw TCP exposure is disabled by default because it has no authentication or
encryption. The installation wizard offers it for each new queue. When the
wizard is run later, choose the existing-printer option to enable it for an
already managed queue. The equivalent advanced command is:

```sh
sudo airprint-server expose-raw BIXOLON-SRP-E300
```

Clients then add a Standard TCP/IP, AppSocket, or JetDirect printer using the
Pi's LAN address and the displayed port. See
[Network printers](docs/network-printers.md) for data-format and security
requirements.

Run the wizard again at any time:

```sh
sudo airprint-server
```

Running the utility without a command opens the interactive setup wizard. It
offers to download and install any supported vendor driver when the selected
printer needs one, so normal setup does not require a separate driver command.
`sudo airprint-server setup` remains available as the explicit equivalent.

For unattended provisioning, skip interaction:

```sh
sudo ./install.sh --no-wizard
```

Use `sudo airprint-server install --wizard` to force the wizard when standard
input is not detected as a terminal. See the
[setup wizard guide](docs/setup-wizard.md).

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

## Updates

After installing a version that contains the updater, check for a new revision:

```sh
sudo airprint-server update --check
```

Review and install it:

```sh
sudo airprint-server update
```

Use `--yes` for unattended confirmation or `--dry-run` to show the planned
revision without changing the system:

```sh
sudo airprint-server update --yes
sudo airprint-server update --dry-run
```

The first update creates a root-owned checkout at
`/var/lib/airprint-server/source`. Updates are restricted to
`https://github.com/xyzhub/airprint-server.git`, branch `main`; the command
refuses a changed remote, non-root-owned or writable source, dirty files,
symbolic links, branch changes, and non-fast-forward history. It shows the
target commit and requires confirmation before downloading or executing the
new installer. Printer setup is not launched and existing managed queues are
preserved.

Older installations without the `update` operation need one manual bootstrap:

```sh
cd ~/airprint-server
git pull --ff-only
sudo ./install.sh --no-wizard
```

See [updating securely](docs/updating.md) for the trust model and recovery
procedure.

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
[setup wizard](docs/setup-wizard.md), [updates](docs/updating.md), and
[troubleshooting](docs/troubleshooting.md).
