import pytest

from airprint_server.validation import (
    ValidationError,
    device_uri,
    host,
    port,
    queue_name,
    socket_uri,
)


@pytest.mark.parametrize("name", ["SwissPOS", "counter-1", "a.b_c"])
def test_queue_names(name: str) -> None:
    assert queue_name(name) == name


@pytest.mark.parametrize("name", ["", "-bad", "bad name", "x;id", "../cups"])
def test_unsafe_queue_names(name: str) -> None:
    with pytest.raises(ValidationError):
        queue_name(name)


def test_host_port_and_socket_uri() -> None:
    assert host("printer.local") == "printer.local"
    assert port("9100") == 9100
    assert socket_uri("192.168.1.123", 9100) == "socket://192.168.1.123:9100"
    assert (
        socket_uri("printer.local", 9100, disable_snmp=True)
        == "socket://printer.local:9100/?snmp=false"
    )


@pytest.mark.parametrize("value", ["a/b", "host:22", "$(id)", "bad host"])
def test_invalid_host(value: str) -> None:
    with pytest.raises(ValidationError):
        host(value)


@pytest.mark.parametrize("value", [0, 65536, "not-a-port"])
def test_invalid_port(value: object) -> None:
    with pytest.raises(ValidationError):
        port(value)  # type: ignore[arg-type]


def test_device_uri_allowlist() -> None:
    assert device_uri("usb://Vendor/Printer?serial=1").startswith("usb:")
    with pytest.raises(ValidationError):
        device_uri("evil://host/value")
    assert device_uri("vendor://host/value", allow_custom=True).startswith("vendor:")

