from fp.battle.state import Battle
from fp.config import FoulPlayConfig, _FoulPlayConfig
from fp.custom.decisions import SearchResult, analyze_decision
from fp.custom.demo import build_demo_battle
from fp.custom.events import EventStore
from fp.custom.opponent_model import update_opponent_tendencies
from fp.custom.report import format_text, summarize
from fp.custom.telemetry import DEFAULT_GUI_SUMMARY_JSON_PATH, _summary_json_path
from fp.run_battle import _extract_request_json


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
                "decision_log": [
                    {
                        "search_time_ms": 100,
                        "policy_top": [
                            {"move": "a", "weight": 0.5},
                            {"move": "b", "weight": 0.48},
                        ],
                    },
                    {
                        "search_time_ms": 300,
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
    assert report["confidence"]["low_confidence_count"] == 1
    assert report["reconnects"] == 1
    assert "Low-confidence decisions" in format_text(report)
