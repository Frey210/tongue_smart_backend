from tongue_smart.main import (
    DEVICE_EVENTS,
    DeviceEvent,
    current_device,
    dashboard_summary,
    health,
    ready,
    receive_device_event,
    sessions,
)


def test_health() -> None:
    assert health() == {"status": "ok"}
    assert ready() == {"status": "ready"}


def test_firmware_compatible_contract() -> None:
    device = current_device()
    assert device["emg_channels"] == 1
    assert device["tongue_pressure_channels"] == 1
    assert device["mqtt"] is False
    assert sessions() == []
    assert dashboard_summary()["device_status"] == "offline"

    DEVICE_EVENTS.clear()
    event = DeviceEvent(
        message_id="boot-123",
        device_id="tongue-smart-v3",
        event="online",
        firmware_version="0.1.0",
        uptime_ms=123,
    )
    assert receive_device_event(event) == {
        "accepted": True, "duplicate": False, "message_id": "boot-123"
    }
    assert receive_device_event(event) == {
        "accepted": True, "duplicate": True, "message_id": "boot-123"
    }
    assert current_device()["connection"] == "online"
