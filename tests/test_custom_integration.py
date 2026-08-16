import json
import subprocess
import sys
from urllib.request import urlopen

from fp.custom.dashboard import start_dashboard, stop_dashboard
from fp.custom.demo import build_demo_battle
from fp.custom.events import publish_event


def _get(url: str) -> tuple[int, str, bytes]:
    with urlopen(url, timeout=3) as response:
        return response.status, response.headers.get_content_type(), response.read()


def test_dashboard_serves_health_state_and_html_assets():
    url = start_dashboard("127.0.0.1", 0)
    try:
        battle = build_demo_battle()
        publish_event("connection_open")
        publish_event("battle_started", battle)

        status, content_type, body = _get(url + "/api/health")
        assert status == 200
        assert content_type == "application/json"
        assert json.loads(body) == {"ok": True}

        status, content_type, body = _get(url + "/api/state")
        state = json.loads(body)
        assert status == 200
        assert content_type == "application/json"
        assert state["connection"] == "connected"
        assert state["battle"]["battle_tag"] == "battle-demo-0001"

        status, content_type, body = _get(url + "/")
        assert status == 200
        assert content_type == "text/html"
        assert b"<html" in body.lower()

        status, content_type, body = _get(url + "/overlay")
        assert status == 200
        assert content_type == "text/html"
        assert b"<html" in body.lower()
    finally:
        stop_dashboard()


def test_dashboard_ephemeral_port_and_idempotent_start():
    first_url = start_dashboard("127.0.0.1", 0)
    try:
        assert not first_url.endswith(":0")
        assert start_dashboard("127.0.0.1", 0) == first_url
    finally:
        stop_dashboard()


def test_demo_cli_can_run_once_and_exit():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fp.custom.demo",
            "--no-open",
            "--once",
            "--port",
            "0",
            "--interval",
            "0",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Dashboard: http://127.0.0.1:" in result.stdout
    assert "Overlay: http://127.0.0.1:" in result.stdout


def test_report_cli_reads_real_jsonl_shape(tmp_path):
    telemetry_path = tmp_path / "battle_summary.jsonl"
    telemetry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "battle_tag": "battle-test-1",
                "winner": "DemoUser",
                "turns": 12,
                "duration_seconds": 45,
                "risk_mode": "balanced",
                "win_reason": "normal",
                "reconnect_count": 1,
                "decision_log": [
                    {
                        "turn": 1,
                        "decision": "earthquake",
                        "search_time_ms": 120,
                        "policy_top": [
                            {"move": "earthquake", "weight": 0.6},
                            {"move": "knockoff", "weight": 0.4},
                        ],
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fp.custom.report",
            str(telemetry_path),
            "--username",
            "DemoUser",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "1W 0L 0T" in result.stdout
    assert "Search ms: avg 120.0" in result.stdout
    assert "Reconnects: 1" in result.stdout
