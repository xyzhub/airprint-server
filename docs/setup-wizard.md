# Interactive setup wizard

The setup wizard is launched automatically by `sudo ./install.sh` when
standard input is a terminal. It can also be started independently:

```sh
sudo airprint-server setup
```

## Guided flow

The wizard first runs CUPS device discovery. Every stable USB device and
detected IPP/IPPS URI is shown as a numbered option. It also offers:

- raw TCP socket printing, normally port 9100;
- a manually supplied IPP or IPPS URI;
- LPD or another complete CUPS device URI;
- finishing without creating a queue.

For USB printers, the make, model, and serial number are decoded from the CUPS
URI. A missing serial number is called out because reconnecting identical
printers may change which physical device receives a job.

Next, the wizard lists only profiles that support the selected connection. It
suggests SwissPOS, Xprinter, 58 mm, 80 mm, or general-purpose profiles from the
detected device name. A detected BIXOLON SRP-E300 selects its dedicated
job-level cutter profile. If its official driver is not installed, queue setup
offers to download it directly from BIXOLON, verifies the pinned package
checksums, and installs only the matching PPD and CPU filter. The operator
always makes the final selection. Generic ESC/POS profiles are not presented
as universally compatible.

Profiles with a built-in driver require no additional driver input. IPP/IPPS
uses CUPS IPP Everywhere. For other general-purpose printers, the wizard
searches `lpinfo -m` for models matching the detected manufacturer and model.
The operator can instead enter an installed model URI or a trusted vendor
`.ppd`/`.ppd.gz` path.

Finally, the wizard validates the queue name and AirPrint display name and
passes the result through the same managed-queue confirmation used by
`add-printer`. It never creates or changes a queue that the operator does not
confirm.

## Unattended installation

Disable the automatic wizard for image building or configuration management:

```sh
sudo ./install.sh --no-wizard
```

The installer remains idempotent, and printers can be configured later with
the wizard or explicit `add-printer` commands.

To force interactive setup when input is not automatically recognized as a
terminal:

```sh
sudo airprint-server install --wizard
```

`--yes` skips the final queue confirmation but does not guess wizard choices.
Use explicit `add-printer` arguments for completely non-interactive queue
creation.
