#!/usr/bin/env python3
"""Tongue Smart ESP device simulator using only the Python standard library."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import getpass
import hashlib
import json
import math
import os
import random
import secrets
import signal
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4


CHANNELS = {
    "emg": ("emg_1", "uV"),
    "tongue_pressure": ("fsr_1", "kPa"),
    "lip_force": ("lip_force_1", "N"),
}


class DeviceClient:
    def __init__(self, base_url: str, device_id: str, api_key: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.device_id = device_id
        self.api_key = api_key
        self.timeout = timeout

    def request(self, method: str, path: str, body: dict | None = None) -> dict | list:
        data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
        headers = {"Accept": "application/json", "Content-Type": "application/json",
                   "X-Device-ID": self.device_id, "X-Device-Key": self.api_key,
                   "User-Agent": "TongueSmart-DeviceSimulator/1.1"}
        request = Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
                return json.loads(payload) if payload else {}
        except HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Network error: {exc.reason}") from exc

    def active_sessions(self) -> list[dict]:
        query = urlencode({"device_id": self.device_id})
        return self.request("GET", f"/device/sessions/active?{query}")  # type: ignore[return-value]

    def send_batch(self, session_id: str, sequence: int, samples: list[dict]) -> dict:
        canonical = json.dumps(samples, sort_keys=True, separators=(",", ":"))
        body = {"message_id": f"sim-{self.device_id}-{uuid4()}", "device_id": self.device_id,
                "sequence": sequence, "checksum": hashlib.sha256(canonical.encode()).hexdigest(), "samples": samples}
        return self.request("POST", f"/sessions/{session_id}/batches", body)  # type: ignore[return-value]


def public_request(base_url: str, method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
    request = Request(base_url.rstrip("/") + path, data=data, method=method, headers={
        "Accept": "application/json", "Content-Type": "application/json",
        "User-Agent": "TongueSmart-DeviceSimulator/1.1",
    })
    try:
        with urlopen(request, timeout=15.0) as response:
            payload = response.read()
            return json.loads(payload) if payload else {}
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error: {exc.reason}") from exc


def sample_value(module: str, elapsed: float, rng: random.Random) -> tuple[float, float]:
    if module == "emg":
        calibrated = max(0.0, 85 + 45 * math.sin(elapsed * 7.0) + rng.gauss(0, 9))
        return calibrated * 3.2, calibrated
    if module == "tongue_pressure":
        calibrated = max(0.0, 13 + 10 * math.sin(elapsed * 1.4) + rng.gauss(0, 0.8))
        return 620 + calibrated * 34, calibrated
    calibrated = max(0.0, 5.5 + 4 * math.sin(elapsed * 0.8) + rng.gauss(0, 0.25))
    return calibrated * 910, calibrated


def make_samples(session: dict, start_index: int, count: int, sample_rate: float, rng: random.Random) -> list[dict]:
    modules = [module for module in session["modules"] if module in CHANNELS]
    stages = session.get("protocol_stages") or ["unspecified"]
    started = time.time()
    samples = []
    for offset in range(count):
        index = start_index + offset
        elapsed = index / sample_rate
        timestamp = datetime.fromtimestamp(started + offset / sample_rate, UTC).isoformat().replace("+00:00", "Z")
        stage = stages[min(len(stages) - 1, int(elapsed // 10) % len(stages))]
        for module in modules:
            channel, unit = CHANNELS[module]
            raw, calibrated = sample_value(module, elapsed, rng)
            samples.append({"timestamp": timestamp, "protocol_stage": stage, "sensor_channel": channel,
                            "raw_value": round(raw, 4), "calibrated_value": round(calibrated, 4),
                            "measurement_unit": unit, "signal_quality": "good"})
    return samples


def choose_session(sessions: list[dict], requested_id: str | None) -> dict:
    if requested_id:
        match = next((item for item in sessions if item["id"] == requested_id or item["session_code"] == requested_id), None)
        if not match:
            raise RuntimeError(f"Active session not found: {requested_id}")
        return match
    if len(sessions) == 1:
        return sessions[0]
    print("Active sessions:")
    for index, item in enumerate(sessions, 1):
        print(f"  {index}. {item['session_code']} | {item['subject_code']} | {', '.join(item['modules'])}")
    selection = int(input("Select session number: "))
    return sessions[selection - 1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulate Tongue Smart ESP HTTPS batch ingest")
    parser.add_argument("--base-url", default="https://tongue-smart.farlabs.my.id/api/v1")
    parser.add_argument("--device-id", default="tongue-smart-v3")
    parser.add_argument("--device-key", help="Unique device secret; defaults to TONGUE_SMART_DEVICE_API_KEY")
    parser.add_argument("--hardware-uid", help="Stable hardware UID; defaults to SIM:<device-id>")
    parser.add_argument("--firmware-version", default="sim-1.1")
    parser.add_argument("--pair", action="store_true", help="Create a pairing code, wait for dashboard claim, then run")
    parser.add_argument("--session", help="Active session UUID or session code")
    parser.add_argument("--duration", type=float, default=30.0, help="Simulation duration in seconds")
    parser.add_argument("--sample-rate", type=float, default=10.0, help="Samples per channel per second")
    parser.add_argument("--batch-size", type=int, default=25, help="Time samples per HTTP batch")
    parser.add_argument("--interval", type=float, default=0.5, help="Delay between batches")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true", help="Print one batch without sending")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.duration <= 0 or args.sample_rate <= 0 or not 1 <= args.batch_size <= 166:
        raise SystemExit("duration/sample-rate must be positive; batch-size must be 1..166")
    api_key = args.device_key or os.getenv("TONGUE_SMART_DEVICE_API_KEY")
    if args.pair:
        api_key = api_key or secrets.token_urlsafe(32)
        pairing = public_request(args.base_url, "POST", "/device/pairings", {
            "device_id": args.device_id,
            "hardware_uid": args.hardware_uid or f"SIM:{args.device_id}",
            "device_secret": api_key,
            "firmware_version": args.firmware_version,
            "capabilities": {"transport": ["https"], "emg_channels": 1,
                             "tongue_pressure_channels": 1, "lip_force": True,
                             "motorized_traction": False, "wifi_portal": False, "mqtt": False},
        })
        print(f"Pairing code: {pairing['pairing_code']}")
        print("Open Dashboard > Perangkat, enter this code, and assign a device name.")
        while True:
            state = public_request(args.base_url, "GET", f"/device/pairings/{pairing['pairing_token']}")
            if state["status"] == "claimed":
                print(f"Device claimed as {state['device_id']}.")
                print("Keep this unique secret for the next run:")
                print(f"  --device-id {args.device_id} --device-key {api_key}")
                break
            if state["status"] == "expired":
                raise SystemExit("Pairing code expired; run --pair again")
            time.sleep(2)
    api_key = api_key or getpass.getpass("Device API key: ")
    if not api_key:
        raise SystemExit("Device API key is required")
    client = DeviceClient(args.base_url, args.device_id, api_key)
    sessions = client.active_sessions()
    if not sessions:
        print("No active sessions. Prepare and start a session from the dashboard first.")
        return 2
    session = choose_session(sessions, args.session)
    rng = random.Random(args.seed)
    sequence = int(session["next_sequence"])
    target = int(args.duration * args.sample_rate)
    sent = 0
    stopped = False

    def stop(*_: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, stop)
    print(f"Simulating {session['session_code']} ({', '.join(session['modules'])}), sequence starts at {sequence}")
    while sent < target and not stopped:
        current = next((item for item in client.active_sessions() if item["id"] == session["id"]), None)
        if current is None:
            print("Session is no longer active. Simulator stopped.")
            break
        control = current.get("control")
        if not control or control["phase"] == "paused":
            print("Waiting for measurement control from dashboard…")
            time.sleep(max(1.0, args.interval))
            continue
        if control["phase"] == "completed":
            print("Measurement stage completed. Waiting for another stage…")
            time.sleep(max(1.0, args.interval))
            continue
        stage_name = control["protocol_stage"]
        if control.get("fsr_point"):
            stage_name = f"{stage_name}:{control['fsr_point']}"
        controlled_session = {**current, "modules": [control["measurement"]], "protocol_stages": [stage_name]}
        count = min(args.batch_size, target - sent)
        samples = make_samples(controlled_session, sent, count, args.sample_rate, rng)
        if args.dry_run:
            print(json.dumps(samples[: min(3, len(samples))], indent=2))
            return 0
        for attempt in range(1, 4):
            try:
                receipt = client.send_batch(session["id"], sequence, samples)
                print(f"ACK seq={receipt['sequence']} receipt={receipt['receipt_id']} duplicate={receipt['duplicate']}")
                sequence += 1
                sent += count
                break
            except RuntimeError as exc:
                if attempt == 3:
                    print(f"Batch failed after 3 attempts: {exc}", file=sys.stderr)
                    return 1
                delay = 2 ** (attempt - 1)
                print(f"Retry {attempt}/3 in {delay}s: {exc}", file=sys.stderr)
                time.sleep(delay)
        time.sleep(max(0, args.interval))
    print(f"Done. Generated {sent} controlled time samples.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
