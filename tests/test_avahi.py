import stat
from pathlib import Path
from xml.etree import ElementTree

import pytest
from conftest import FakeRunner

from airprint_server import avahi
from airprint_server.config import ManagedPrinter, State


def test_reconciles_named_bonjour_service_for_each_dedicated_raw_printer(
    tmp_path: Path,
) -> None:
    printer = ManagedPrinter(
        "BIXOLON-SRP-E300",
        "Kitchen & Bar <Receipt>",
        "bixolon-srp-e300",
        "usb://BIXOLON/SRP-E300?serial=1",
        "usb",
        raw_port=9100,
        raw_address="192.168.1.240",
        raw_interface="wlan0",
    )
    state = State(printers={printer.name: printer})
    service_dir = tmp_path / "services"
    hosts_path = tmp_path / "hosts"
    hosts_path.write_text("192.168.1.10 existing-device.local\n", encoding="utf-8")
    hosts_path.chmod(0o640)
    runner = FakeRunner()

    avahi.reconcile_raw_printer_services(
        runner,  # type: ignore[arg-type]
        state,
        service_dir=service_dir,
        hosts_path=hosts_path,
    )

    assert len(state.avahi_services) == 1
    service_path = Path(state.avahi_services[0])
    assert service_path.parent == service_dir
    document = ElementTree.parse(service_path)
    assert document.findtext("name") == "Kitchen & Bar <Receipt>"
    service = document.find("service")
    assert service is not None
    assert service.findtext("type") == "_pdl-datastream._tcp"
    assert service.findtext("host-name") == "bixolon-srp-e300-printer.local"
    assert service.findtext("port") == "9100"
    assert "192.168.1.10 existing-device.local" in hosts_path.read_text(encoding="utf-8")
    assert (
        "192.168.1.240 bixolon-srp-e300-printer.local"
        in hosts_path.read_text(encoding="utf-8")
    )
    assert stat.S_IMODE(hosts_path.stat().st_mode) == 0o640
    assert ("systemctl", "restart", "avahi-daemon.service") in runner.calls


def test_reconcile_removes_only_managed_discovery_records(tmp_path: Path) -> None:
    printer = ManagedPrinter(
        "Receipt",
        "Receipt",
        None,
        "usb://Vendor/Receipt",
        "usb",
        raw_port=9100,
        raw_address="192.168.1.240",
        raw_interface="wlan0",
    )
    state = State(printers={printer.name: printer})
    service_dir = tmp_path / "services"
    hosts_path = tmp_path / "hosts"
    hosts_path.write_text("192.168.1.10 existing-device.local\n", encoding="utf-8")
    runner = FakeRunner()
    avahi.reconcile_raw_printer_services(
        runner,  # type: ignore[arg-type]
        state,
        service_dir=service_dir,
        hosts_path=hosts_path,
    )
    managed_path = Path(state.avahi_services[0])

    printer.raw_port = None
    printer.raw_address = None
    printer.raw_interface = None
    avahi.reconcile_raw_printer_services(
        runner,  # type: ignore[arg-type]
        state,
        service_dir=service_dir,
        hosts_path=hosts_path,
    )

    assert not managed_path.exists()
    assert state.avahi_services == []
    assert hosts_path.read_text(encoding="utf-8") == "192.168.1.10 existing-device.local\n"


def test_normalized_printer_name_collisions_get_distinct_hostnames() -> None:
    printers = {
        name: ManagedPrinter(
            name,
            name,
            None,
            f"usb://Vendor/{name}",
            "usb",
            raw_port=9100,
            raw_address=address,
            raw_interface="wlan0",
        )
        for name, address in (
            ("Receipt_One", "192.168.1.240"),
            ("Receipt-One", "192.168.1.241"),
        )
    }

    hostnames = avahi.raw_printer_hostnames(State(printers=printers))

    assert len(set(hostnames.values())) == 2
    assert all(hostname.startswith("receipt-one-") for hostname in hostnames.values())
    assert all(hostname.endswith("-printer.local") for hostname in hostnames.values())


def test_bonjour_service_name_is_limited_to_one_dns_label(tmp_path: Path) -> None:
    printer = ManagedPrinter(
        "Receipt",
        "Kitchen Receipt Printer " * 5,
        None,
        "usb://Vendor/Receipt",
        "usb",
        raw_port=9100,
        raw_address="192.168.1.240",
        raw_interface="wlan0",
    )
    state = State(printers={printer.name: printer})

    avahi.reconcile_raw_printer_services(
        FakeRunner(),  # type: ignore[arg-type]
        state,
        service_dir=tmp_path / "services",
        hosts_path=tmp_path / "hosts",
    )

    advertised_name = ElementTree.parse(Path(state.avahi_services[0])).findtext("name")
    assert advertised_name is not None
    assert len(advertised_name.encode("utf-8")) <= 63


def test_duplicate_display_names_are_disambiguated_by_queue_name(tmp_path: Path) -> None:
    printers = {
        name: ManagedPrinter(
            name,
            "Kitchen Receipt",
            None,
            f"usb://Vendor/{name}",
            "usb",
            raw_port=9100,
            raw_address=address,
            raw_interface="wlan0",
        )
        for name, address in (
            ("Kitchen-1", "192.168.1.240"),
            ("Kitchen-2", "192.168.1.241"),
        )
    }
    state = State(printers=printers)

    avahi.reconcile_raw_printer_services(
        FakeRunner(),  # type: ignore[arg-type]
        state,
        service_dir=tmp_path / "services",
        hosts_path=tmp_path / "hosts",
    )

    names = {
        ElementTree.parse(Path(path)).findtext("name") for path in state.avahi_services
    }
    assert names == {"Kitchen Receipt (Kitchen-1)", "Kitchen Receipt (Kitchen-2)"}


def test_reconcile_refuses_to_replace_an_unmanaged_service_file(tmp_path: Path) -> None:
    printer = ManagedPrinter(
        "Receipt",
        "Receipt",
        None,
        "usb://Vendor/Receipt",
        "usb",
        raw_port=9100,
        raw_address="192.168.1.240",
        raw_interface="wlan0",
    )
    state = State(printers={printer.name: printer})
    service_dir = tmp_path / "services"
    hosts_path = tmp_path / "hosts"
    avahi.reconcile_raw_printer_services(
        FakeRunner(),  # type: ignore[arg-type]
        state,
        service_dir=service_dir,
        hosts_path=hosts_path,
    )
    service_path = Path(state.avahi_services[0])
    service_path.write_text("unrelated service\n", encoding="utf-8")
    state.avahi_services = []

    with pytest.raises(RuntimeError, match="unmanaged Avahi service"):
        avahi.reconcile_raw_printer_services(
            FakeRunner(),  # type: ignore[arg-type]
            state,
            service_dir=service_dir,
            hosts_path=hosts_path,
        )


def test_reconcile_refuses_an_avahi_hosts_symlink(tmp_path: Path) -> None:
    target = tmp_path / "real-hosts"
    target.write_text("192.168.1.10 existing.local\n", encoding="utf-8")
    hosts_path = tmp_path / "hosts"
    hosts_path.symlink_to(target)

    with pytest.raises(RuntimeError, match="symbolic link"):
        avahi.reconcile_raw_printer_services(
            FakeRunner(),  # type: ignore[arg-type]
            State(),
            service_dir=tmp_path / "services",
            hosts_path=hosts_path,
        )

    assert target.read_text(encoding="utf-8") == "192.168.1.10 existing.local\n"
