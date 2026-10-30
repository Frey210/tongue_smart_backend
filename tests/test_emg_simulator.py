import importlib.util
from pathlib import Path
from unittest.mock import Mock


spec = importlib.util.spec_from_file_location("device_simulator", Path(__file__).parents[1] / "tools/device_simulator.py")
simulator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(simulator)


def test_simulator_waits_for_capture_duration_and_operator_confirmation():
    client, starts = Mock(), {}
    session = {"id": "s", "control": {"id": "c", "measurement": "emg", "phase": "baseline"},
               "emg_baseline": {"state": "requested"}}
    assert simulator.simulate_emg_baseline(client, session, starts, 100)
    assert client.request.call_args.args[2]["state"] == "capturing"
    session["emg_baseline"]["state"] = "capturing"
    client.reset_mock()
    simulator.simulate_emg_baseline(client, session, starts, 112)
    client.request.assert_not_called()
    simulator.simulate_emg_baseline(client, session, starts, 113)
    assert client.request.call_args.args[2]["state"] == "review"
    session["emg_baseline"]["state"] = "review"
    client.reset_mock()
    simulator.simulate_emg_baseline(client, session, starts, 120)
    client.request.assert_not_called()
    session["emg_baseline"]["state"] = "accepted"
    simulator.simulate_emg_baseline(client, session, starts, 121)
    assert client.request.call_args.args[2]["state"] == "ready"
    session["control"]["phase"] = "recording"
    assert not simulator.simulate_emg_baseline(client, session, starts, 122)
