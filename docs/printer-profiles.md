# Printer profiles

Profiles contain model behavior, not connection or address. Built-ins are
packaged with the application. Put local YAML in
`/etc/airprint-server/profiles.d/`; a local file with the same ID overrides a
built-in without changing the packaged copy.

```yaml
id: example-80mm
display_name: Example 80 mm receipt printer
category: escpos
status: community-tested
driver: drv:///escpos.drv/gp80160.ppd
paper_width_mm: 80
printable_width_mm: 72
page_size: w226h842
resolution: 203dpi
color_model: Gray
monochrome: true
cutter: false
supported_connections:
  - socket
  - usb
cups_options:
  print-color-mode: monochrome
```

Required fields are `id`, `display_name`, `category`, `status`, and a non-empty
`supported_connections` list. Status must be `tested`, `community-tested`,
`unverified`, or `generic`. Connections may be `socket`, `usb`, `ipp`, `ipps`,
`lpd`, or `custom-uri`. Widths must be positive sensible millimeter values and
printable width cannot exceed paper width.

Profile defaults become queue-specific `lpadmin -o` arguments. They are never
set globally. Command-line `--driver` or `--ppd` takes precedence over the
profile driver. The generic driverless profile intentionally has no driver;
IPP/IPPS uses `everywhere`, while non-IPP connections require a driver or PPD.

Generic ESC/POS profiles are uncalibrated starting points. Test paper width,
heat/density, raster output, feed, and cutter behavior on the exact firmware.

## BIXOLON SRP-E300

### Official BIXOLON driver

The preferred configuration uses a user-supplied copy of BIXOLON Linux POS
CUPS Driver v1.5.9:

```sh
sudo airprint-server install-bixolon-driver \
  ./Software_BxlPOSCupsDrv_Linux_v1.5.9.tgz
```

The archive is not redistributed because of its vendor license. The command
validates its SHA-256 and structure before installing the matching CPU filter
and `SRPE300_v1.0.3.ppd`. Managed SRP-E300 queues can be migrated during the
same operation with these official PPD defaults:

```yaml
PageSize: 61X72MMY70MM
Resolution: 180dpi
ColorModel: 1Gray
PageType: 0Variable
Dithering: 1True
PageCut: 4JobCutFeed
print-scaling-default: fit
```

`PageCut=4JobCutFeed` cuts and feeds once at the end of the job. Print
darkness remains a persistent printer VMSM setting; the PPD does not expose
thermal heat adjustment.

### Generic fallback

The SRP-E300 uses 79.5 mm roll paper but has a 72 mm print area at 180 dpi.
The `bixolon-srp-e300` profile therefore uses a 72 mm custom CUPS page instead
of rasterizing across the full roll width:

```yaml
page_size: Custom.72x297mm
resolution: 180dpi
```

The rastertoescpos GP-80160II compatibility PPD supports both this custom media
size and 180 dpi. Restricting the CUPS raster to the printer's actual print
area prevents content on the right from being sent beyond the print head.

The profile also sets:

```yaml
cups_options:
  escCutter: "1"
```

In rastertoescpos, `escCutter=1` emits one cut after every page in the CUPS
raster stream has completed. This is a per-job cut. `escCutter=2` cuts from the
page-finalization path and therefore cuts after every page. The SRP-E300 profile
must not use value `2`.

The profile is labelled `unverified` until output dimensions, raster quality,
feed distance, and cutter behavior are confirmed on physical SRP-E300
hardware.

For an existing queue, inspect and change the effective CUPS option with:

```sh
lpoptions -p BIXOLON-SRP-E300 -l | grep -E 'PageSize|Resolution|escCutter'
sudo lpadmin -p BIXOLON-SRP-E300 \
  -o PageSize=Custom.72x297mm \
  -o Resolution=180dpi \
  -o escCutter=1
```

The direct `lpadmin` command takes effect immediately. To keep
airprint-server's managed state aligned with that queue, update the application
and run the setup wizard again with the same queue name and the
`bixolon-srp-e300` profile.
