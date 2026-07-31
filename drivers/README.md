# Vendor drivers

This directory is the local drop-in location for printer drivers that cannot
be redistributed by this project.

## BIXOLON Linux POS CUPS driver

The normal installation downloads the Linux POS CUPS driver directly from the
[official BIXOLON SRP-E300 support page](https://www.bixolon.com/download_view.php?idx=73):

```sh
sudo airprint-server install-bixolon-driver
```

For an offline installation, download it separately and place the inner
archive here:

```text
drivers/Software_BxlPOSCupsDrv_Linux_v1.5.9.tgz
```

The archive is intentionally ignored by Git. BIXOLON's included license grants
a personal, non-sublicensable license and restricts copying and electronic
transfer, so the proprietary PPD files and executable filters are downloaded
from BIXOLON or supplied locally rather than redistributed by this repository.

The expected archive metadata is recorded in
[`bixolon-pos-cups-v1.5.9.yaml`](bixolon-pos-cups-v1.5.9.yaml). Check the
archive before using it:

```sh
sha256sum drivers/Software_BxlPOSCupsDrv_Linux_v1.5.9.tgz
```

Install a locally supplied archive with:

```sh
sudo airprint-server install-bixolon-driver \
  drivers/Software_BxlPOSCupsDrv_Linux_v1.5.9.tgz
```

Both paths validate pinned checksums and the complete archive structure, select
the current CPU architecture, install only the SRP-E300 PPD and CUPS filter,
verify runtime linkage, and offer to migrate managed SRP-E300 queues. Passing
`--yes` accepts the license and queue-migration confirmations for unattended
installation.

Do not run the archive's `setup_v1.5.9.sh` from automation. It contains broad
removal patterns for previously installed BIXOLON files and legacy logic that
can replace the system CUPS USB backend. airprint-server integration should
instead be used so only the exact model PPD and architecture-specific filter
are installed.

## XPrinter POS CUPS driver

Install the current ARM-capable XPrinter driver directly from the
[official XPrinter download page](https://www.xprintertech.com/download.html):

```sh
sudo airprint-server install-xprinter-driver
```

The EULA inside v3.13.11 expressly prohibits redistribution outside the
licensee's organization. Consequently, the repository stores only
[`xprinter-pos-cups-v3.13.11.yaml`](xprinter-pos-cups-v3.13.11.yaml), while the
installer downloads the proprietary RAR from XPrinter after confirmation.
Both the RAR and its inner Debian package are checksum-verified.

For offline installation, pass a previously downloaded official RAR or its
inner Debian package:

```sh
sudo airprint-server install-xprinter-driver \
  drivers/printer-driver-pos_3.13.11_all.deb
```

The integration does not install the Debian package directly. It safely reads
the package and installs only the architecture-specific filters and POS-58,
POS-76, and POS-80 PPDs, avoiding the vendor maintainer script's unrelated
cron, USB-quirk, and automatic-queue changes.

The supplied 2019 `cupsdrv-2.4.0` XP-58, XP-76, and XP-80 installers contain
only i386 and x86-64 Linux filters and cannot run on Raspberry Pi. Their hashes
and replacement are documented in
[`xprinter-pos-cups-legacy-v2.4.0.yaml`](xprinter-pos-cups-legacy-v2.4.0.yaml);
the proprietary files remain ignored by Git.
