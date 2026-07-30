from airprint_server.diagnostics import Check


def test_diagnostic_formatting() -> None:
    assert Check(True, "CUPS running").format() == "[OK] CUPS running"
    text = Check(False, "Queue exists", "missing", "lpstat -p Queue").format()
    assert text == "[FAIL] Queue exists: missing\n       Action: lpstat -p Queue"
    assert Check(True, "ipp-usb", warning=True).format().startswith("[WARN]")

