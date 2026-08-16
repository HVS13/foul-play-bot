from fp.battle.state import Battle
from fp.config import _FoulPlayConfig
from fp.custom.decisions import SearchResult, analyze_decision
from fp.custom.events import EventStore
from fp.custom.opponent_model import update_opponent_tendencies
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
