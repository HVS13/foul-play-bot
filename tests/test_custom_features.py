import asyncio

from fp.battle.state import Battle
from fp.config import FoulPlayConfig, RiskModes, _FoulPlayConfig
from fp.custom.decisions import SearchResult, analyze_decision
from fp.custom.demo import build_demo_battle
from fp.custom.events import EventStore
from fp.custom.opponent_model import update_opponent_tendencies
from fp.custom.report import format_text, summarize
from fp.custom.telemetry import (
    DEFAULT_GUI_SUMMARY_JSON_PATH,
    _summary_json_path,
    record_decision,
)
from fp.run_battle import _apply_room_rename, _extract_request_json
from fp.search.main import _adjust_search, _allocate_search_pass_times
from fp.websocket_client import PSWebsocketClient


def test_decision_analysis_is_shared_and_deterministic():
    protect = analyze_decision(None, "protect")
    assert "protect" in protect.tags
    assert "status" in protect.tags

    switch = analyze_decision(None, "switch pikachu")
    assert switch.move_id is None
    assert switch.tags == ("switch",)


def test_search_result_serializes_for_dashboard():
    result = SearchResult(
        choice="protect",
        policy=[("protect", 0.6), ("tackle", 0.4)],
        confidence_ratio=1.5,
        sampled_states=4,
        search_passes=1,
        search_time_per_state_ms=100,
        total_search_time_ms=240,
        risk_mode="balanced",
    ).to_dict()

    assert result["choice"] == "protect"
    assert result["policy"][0]["weight"] == 0.6
    assert result["confidence_ratio"] == 1.5


def test_event_store_keeps_latest_state_and_recent_events():
    store = EventStore(max_events=2)
    battle = Battle("battle-test-1")
    battle.pokemon_format = "gen9randombattle"

    store.publish("battle_started", battle)
    store.publish("search_started", battle, sampled_states=2)
    store.publish("decision_ready", battle, result={"choice": "protect"})
    snapshot = store.snapshot()

    assert snapshot["status"] == "decision_ready"
    assert snapshot["battle"]["battle_tag"] == "battle-test-1"
    assert snapshot["decision"]["result"]["choice"] == "protect"
    assert len(snapshot["events"]) == 2


def test_resume_request_extraction_uses_highest_rqid():
    request = _extract_request_json(
        [
            '|request|{"rqid": 4, "wait": true}',
            '|request|{"rqid": 7, "wait": false}',
            '|request|{"rqid": 6, "wait": true}',
        ]
    )
    assert request["rqid"] == 7
    assert request["wait"] is False


def test_auto_parallelism_accounts_for_threads(monkeypatch):
    monkeypatch.setattr("fp.config.os.cpu_count", lambda: 9)
    assert _FoulPlayConfig._auto_parallelism(8, search_threads=1) == 8
    assert _FoulPlayConfig._auto_parallelism(8, search_threads=2) == 4
    assert _FoulPlayConfig._auto_parallelism(8, search_threads=4) == 2


def test_search_adjustment_preserves_mode_budget_and_scales_only_for_timer():
    battle = Battle("battle-search-budget")
    battle.team_preview = False
    battle.turn = 40

    battle.time_remaining = None
    assert _adjust_search(battle, 8, 1000) == (8, 1000)

    battle.time_remaining = 50
    assert _adjust_search(battle, 8, 1000) == (8, 750)

    battle.time_remaining = 20
    assert _adjust_search(battle, 8, 1000) == (8, 500)


def test_low_confidence_retry_is_reserved_inside_original_budget():
    first_pass, retry = _allocate_search_pass_times(1500, allow_retry=True)
    assert (first_pass, retry) == (1200, 300)
    assert first_pass + retry == 1500

    assert _allocate_search_pass_times(1500, allow_retry=False) == (1500, 0)
    assert _allocate_search_pass_times(80, allow_retry=True) == (80, 0)


def test_opponent_model_counts_only_voluntary_switches():
    battle = Battle("battle-test-2")
    battle.opponent.name = "p2"

    update_opponent_tendencies(
        battle,
        "\n".join(
            [
                "|drag|p2a: Foo|Bar, L50|100/100",
                "|switch|p2a: Foo|Baz, L50|100/100",
                "|move|p2a: Foo|Protect|p1a: Bar",
            ]
        ),
    )

    assert battle.opponent_tendencies == {
        "actions": 2,
        "moves": 1,
        "switches": 1,
        "protects": 1,
    }


def test_gui_defaults_to_persistent_json_telemetry(monkeypatch):
    monkeypatch.setattr(FoulPlayConfig, "gui", True, raising=False)
    monkeypatch.setattr(FoulPlayConfig, "summary_json_path", None, raising=False)
    assert _summary_json_path() == DEFAULT_GUI_SUMMARY_JSON_PATH

    monkeypatch.setattr(
        FoulPlayConfig, "summary_json_path", "custom/session.jsonl", raising=False
    )
    assert _summary_json_path() == "custom/session.jsonl"


def test_demo_battle_is_usable_by_event_snapshot():
    battle = build_demo_battle()
    store = EventStore()
    store.publish("battle_started", battle)
    snapshot = store.snapshot()

    assert snapshot["battle"]["battle_tag"] == "battle-demo-0001"
    assert snapshot["battle"]["user"]["active"]["name"] == "greattusk"
    assert snapshot["battle"]["opponent"]["active"]["name"] == "kingambit"


def test_room_rename_updates_transport_and_battle_state(monkeypatch):
    old_room = "battle-gen9randombattle-123"
    new_room = old_room + "-hiddenhash"
    message = ">{}\n|noinit|rename|{}|".format(old_room, new_room)

    client = PSWebsocketClient()
    client.rooms = {old_room}
    client.room_aliases = {}
    client.recent_room_commands = {}
    assert client.parse_room_rename(message) == (old_room, new_room)

    client.register_room_rename(old_room, new_room)
    assert client.resolve_room(old_room) == new_room
    assert old_room not in client.rooms
    assert new_room in client.rooms

    battle = Battle(old_room)
    monkeypatch.setattr("fp.run_battle.write_last_battle_tag", lambda _: None)
    assert _apply_room_rename(client, battle, message) is True
    assert battle.battle_tag == new_room
    assert battle.room_rename_count == 1


def test_rejected_command_is_retried_in_renamed_room():
    old_room = "battle-gen9randombattle-123"
    new_room = old_room + "-hiddenhash"
    client = PSWebsocketClient()
    client.rooms = {new_room}
    client.room_aliases = {old_room: new_room}
    client.recent_room_commands = {"/choose": (old_room, ["/choose move tackle", "7"])}
    sent = []

    async def fake_send(room, message_list):
        sent.append((room, message_list))
        return True

    client.send_message = fake_send
    error = "|pm|Bot|~|/error /choose - must be used in a chat room, not a console"
    assert asyncio.run(client._retry_rejected_room_command(error)) is True
    assert sent == [(new_room, ["/choose move tackle", "7"])]


def test_record_decision_keeps_search_effort_metadata(monkeypatch):
    battle = Battle("battle-test-telemetry")
    battle.rqid = 12
    battle.turn = 8
    battle.time_remaining = 27
    monkeypatch.setattr(FoulPlayConfig, "risk_mode", RiskModes.auto, raising=False)
    result = SearchResult(
        choice="protect",
        policy=[("protect", 0.55), ("tackle", 0.45)],
        confidence_ratio=1.2222,
        sampled_states=8,
        search_passes=2,
        search_time_per_state_ms=1500,
        total_search_time_ms=3200,
        risk_mode="safe",
    )

    entry = record_decision(
        battle,
        "protect",
        3300,
        result.policy,
        search_result=result,
    )

    assert entry["rqid"] == 12
    assert entry["time_remaining"] == 27
    assert entry["configured_risk_mode"] == "auto"
    assert entry["confidence_ratio"] == 1.2222
    assert entry["sampled_states"] == 8
    assert entry["search_passes"] == 2
    assert entry["mcts_time_per_state_ms"] == 1500
    assert entry["mcts_wall_time_ms"] == 3200
    assert entry["resolved_risk_mode"] == "safe"


def test_telemetry_report_surfaces_search_and_confidence():
    report = summarize(
        [
            {
                "winner": "Bot",
                "win_reason": "normal",
                "turns": 20,
                "duration_seconds": 300,
                "risk_mode": "auto",
                "reconnect_count": 1,
                "room_rename_count": 1,
                "decision_log": [
                    {
                        "search_time_ms": 100,
                        "time_remaining": 25,
                        "configured_risk_mode": "auto",
                        "resolved_risk_mode": "aggressive",
                        "confidence_ratio": 1.04,
                        "sampled_states": 8,
                        "search_passes": 2,
                        "mcts_time_per_state_ms": 1000,
                        "policy_top": [
                            {"move": "a", "weight": 0.5},
                            {"move": "b", "weight": 0.48},
                        ],
                    },
                    {
                        "search_time_ms": 300,
                        "time_remaining": 80,
                        "configured_risk_mode": "auto",
                        "resolved_risk_mode": "balanced",
                        "sampled_states": 4,
                        "search_passes": 1,
                        "mcts_time_per_state_ms": 800,
                        "policy_top": [
                            {"move": "a", "weight": 0.7},
                            {"move": "b", "weight": 0.2},
                        ],
                    },
                ],
            }
        ],
        username="Bot",
    )

    assert report["record"]["wins"] == 1
    assert report["search_time_ms"]["avg"] == 200.0
    assert report["search_effort"]["avg_sampled_states"] == 6.0
    assert report["search_effort"]["avg_mcts_time_per_state_ms"] == 900.0
    assert report["search_effort"]["extra_pass_decisions"] == 1
    assert report["search_effort"]["extra_pass_pct"] == 50.0
    assert report["confidence"]["low_confidence_count"] == 1
    assert report["reconnects"] == 1
    assert report["room_renames"] == 1
    assert report["timer_pressure_decisions"] == 1
    assert report["decision_risk_modes"] == {"auto": 2}
    assert report["resolved_risk_modes"] == {"aggressive": 1, "balanced": 1}
    assert "Search effort" in format_text(report)
    assert "Low-confidence decisions" in format_text(report)
    assert "room renames" in format_text(report)
