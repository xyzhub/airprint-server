# USB printers

USB discovery is delegated to CUPS:

```sh
airprint-server discover-usb
lpinfo -v
```

Always configure the returned `usb://` URI; do not use `/dev/usb/lp0`. The
interactive add flow shows manufacturer, model, serial where present, and the
full URI. It warns about unstable model-only URIs and indistinguishable
identical devices.

If no device appears, check:

1. Printer power, data cable, and `lsusb`.
2. `systemctl status cups` and the executable CUPS USB backend.
3. `journalctl -u cups -u ipp-usb`.
4. Whether `ipp-usb` claims an IPP-over-USB printer.

The installer does not install or disable `ipp-usb` automatically. The
explicit `--disable-ipp-usb` add option records whether it was installed,
enabled, and active before changing it. Uninstall restores that state. To
restore manually, use `sudo systemctl enable --now ipp-usb` if it was
previously enabled and active.

When a configured printer is disconnected, diagnostics mark both USB
detection and exact URI presence. Permissions/backend failures are reported
separately from an empty device list.

