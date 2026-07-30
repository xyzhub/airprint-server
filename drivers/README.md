# Vendor drivers

This directory is the local drop-in location for printer drivers that cannot
be redistributed by this project.

## BIXOLON Linux POS CUPS driver

Download the Linux POS CUPS driver from the
[official BIXOLON SRP-E300 support page](https://www.bixolon.com/download_view.php?idx=73)
and place the archive here:

```text
drivers/Software_BxlPOSCupsDrv_Linux_v1.5.9.tgz
```

The archive is intentionally ignored by Git. BIXOLON's included license grants
a personal, non-sublicensable license and restricts copying and electronic
transfer, so the proprietary PPD files and executable filters must not be
committed to or redistributed by this repository.

The expected archive metadata is recorded in
[`bixolon-pos-cups-v1.5.9.yaml`](bixolon-pos-cups-v1.5.9.yaml). Check the
archive before using it:

```sh
sha256sum drivers/Software_BxlPOSCupsDrv_Linux_v1.5.9.tgz
```

Install the locally supplied archive with:

```sh
sudo airprint-server install-bixolon-driver \
  drivers/Software_BxlPOSCupsDrv_Linux_v1.5.9.tgz
```

The command validates the pinned checksum and complete archive structure,
selects the current CPU architecture, installs only the SRP-E300 PPD and CUPS
filter, verifies runtime linkage, and offers to migrate managed SRP-E300
queues. Passing `--yes` accepts the license and queue-migration confirmations
for unattended installation.

Do not run the archive's `setup_v1.5.9.sh` from automation. It contains broad
removal patterns for previously installed BIXOLON files and legacy logic that
can replace the system CUPS USB backend. airprint-server integration should
instead be used so only the exact model PPD and architecture-specific filter
are installed.
