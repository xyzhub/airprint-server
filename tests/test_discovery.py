from airprint_server.discovery import parse_lpinfo_devices, parse_usb_uri


def test_parse_usb_discovery() -> None:
    output = """network ipp
direct usb://XPrinter/XP-80C?serial=123456
direct usb://Acme/Thermal%20Printer
"""
    devices = parse_lpinfo_devices(output)
    assert len(devices) == 2
    assert devices[0].manufacturer == "XPrinter"
    assert devices[0].model == "XP-80C"
    assert devices[0].serial == "123456"
    assert devices[0].stable
    assert not devices[1].stable


def test_parse_duplicate_lines_once() -> None:
    line = "direct usb://Vendor/Model?serial=A\n"
    assert len(parse_lpinfo_devices(line + line)) == 1
    assert parse_usb_uri("usb://Vendor/Model").model == "Model"

