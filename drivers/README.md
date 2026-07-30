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
