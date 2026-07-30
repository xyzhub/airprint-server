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

The `bixolon-srp-e300` profile uses the rastertoescpos 80 mm model and sets:

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
lpoptions -p BIXOLON-SRP-E300 -l | grep escCutter
sudo lpadmin -p BIXOLON-SRP-E300 -o escCutter=1
```

The direct `lpadmin` command takes effect immediately. To keep
airprint-server's managed state aligned with that queue, update the application
and run the setup wizard again with the same queue name and the
`bixolon-srp-e300` profile.
