import copy
import json
import logging
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from fp.config import BotModes, CustomFormatter, FoulPlayConfig, _FoulPlayConfig
from fp.battle.protocol import request as apply_request
from fp.battle.state import Battle
from fp.custom.browser import BrowserSession
from fp.custom.dashboard import start_dashboard, stop_dashboard
from fp.custom.events import EventStore


def test_local_log_messages_include_formatted_urls():
    record = logging.LogRecord(
        "dashboard",
        logging.INFO,
        "",
        0,
        "Dashboard: %s",
        ("http://127.0.0.1:8765",),
        None,
    )
    assert (
        CustomFormatter().format(record) == "INFO     Dashboard: http://127.0.0.1:8765"
    )


def test_request_leaves_preview_when_normal_battle_starts():
    battle = Battle("battle-gen9ou-1")
    apply_request(battle, ["", "request", '{"rqid": 1, "teamPreview": true}'])
    assert battle.team_preview
    apply_request(battle, ["", "request", '{"rqid": 2, "active": [{}]}'])
    assert not battle.team_preview


@pytest.fixture
def snapshot(monkeypatch):
    for name, value in {
        "pokemon_format": "gen9ou",
        "battle_timer": "none",
        "log_to_file": False,
        "suggest_only": True,
        "risk_mode": None,
    }.items():
        monkeypatch.setattr(FoulPlayConfig, name, value, raising=False)
    monkeypatch.setattr("fp.run_battle._initialize_resume_datasets", lambda *args: None)
    monkeypatch.setattr("fp.run_battle.write_last_battle_tag", lambda *args: None)
    return {
        "client_id": "test-tab",
        "battle_tag": "battle-gen9ou-1234",
        "pokemon_format": "gen9ou",
        "history": [
            "|gametype|singles",
            "|player|p1|TestUser|1",
            "|player|p2|Opponent|2",
            "|poke|p2|Pikachu, L100|",
            "|poke|p2|Blastoise, L100|",
            "|start|",
            "|switch|p1a: Bulbasaur|Bulbasaur, L100|200/200",
            "|switch|p2a: Pikachu|Pikachu, L100|100/100",
            "|turn|1",
        ],
        "request": {
            "rqid": 1,
            "active": [
                {
                    "moves": [
                        {
                            "move": "Tackle",
                            "id": "tackle",
                            "pp": 35,
                            "maxpp": 35,
                            "target": "normal",
                            "disabled": False,
                        }
                    ]
                }
            ],
            "side": {
                "id": "p1",
                "name": "TestUser",
                "pokemon": [
                    {
                        "ident": "p1: Bulbasaur",
                        "details": "Bulbasaur, L100",
                        "condition": "200/200",
                        "active": True,
                        "stats": {
                            "atk": 100,
                            "def": 100,
                            "spa": 100,
                            "spd": 100,
                            "spe": 100,
                        },
                        "moves": ["tackle"],
                        "baseAbility": "overgrow",
                        "ability": "overgrow",
                        "item": "",
                        "teraType": "Grass",
                    }
                ],
            },
        },
    }


def test_browser_config_needs_no_password_or_second_login(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["run.py", "--bot-mode", "browser", "--pokemon-format", "gen9ou"]
    )
    config = _FoulPlayConfig()
    config.configure()
    assert config.gui and config.suggest_only
    assert config.battle_timer == "none"
    assert config.username is None and config.websocket_uri is None


def test_browser_config_rejects_public_binding(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run.py",
            "--bot-mode",
            "browser",
            "--pokemon-format",
            "gen9ou",
            "--gui-host",
            "0.0.0.0",
        ],
    )
    with pytest.raises(AssertionError, match="127.0.0.1"):
        _FoulPlayConfig().configure()


def test_browser_rebuild_and_delta_preserve_unplayed_opponent_and_current_request(
    snapshot, monkeypatch
):
    captured = []

    async def pick(battle):
        captured.append(copy.deepcopy(battle))
        return ["/choose move tackle", str(battle.rqid)]

    monkeypatch.setattr("fp.custom.browser.async_pick_move", pick)
    session = BrowserSession()
    first = session.analyze(snapshot)
    assert first["command"] == "/choose move tackle|1"
    assert captured[0].opponent.active.name == "pikachu"
    assert [p.name for p in captured[0].opponent.reserve] == ["blastoise"]
    assert captured[0].user.active.hp == 200
    assert session.analyze(snapshot) == first
    assert len(captured) == 1
    snapshot["history"] += ["|-damage|p1a: Bulbasaur|150/200", "|turn|2"]
    snapshot["request"]["rqid"] = 2
    snapshot["request"]["side"]["pokemon"][0]["condition"] = "150/200"
    second = session.analyze(snapshot)
    assert second["command"] == "/choose move tackle|2"
    assert captured[-1].turn == 2 and captured[-1].user.active.hp == 150
    assert len(captured[-1].opponent.reserve) == 1


@pytest.mark.parametrize("active", [False, True])
def test_browser_preview_uses_upstream_team_preview(snapshot, monkeypatch, active):
    snapshot["history"] = snapshot["history"][:5] + ["|teampreview|"]
    snapshot["request"].pop("active")
    snapshot["request"]["teamPreview"] = True
    snapshot["request"]["side"]["pokemon"][0]["active"] = active

    async def preview(battle, client):
        assert battle.team_preview
        assert len(battle.user.reserve) == 1
        assert len(battle.opponent.reserve) == 2
        await client.send_message(battle.battle_tag, ["/team 1|1"])

    monkeypatch.setattr(
        "fp.modes.standard_battle.StandardBattleMode.handle_team_preview",
        lambda self, b, c: preview(b, c),
    )
    assert BrowserSession().analyze(snapshot)["command"] == "/team 1|1"


def test_browser_does_not_recommend_after_finish_or_wait(snapshot, monkeypatch):
    monkeypatch.setattr(
        "fp.custom.browser.async_pick_move", lambda *_: pytest.fail("Must not search")
    )
    snapshot["request"]["wait"] = True
    assert BrowserSession().analyze(snapshot)["command"] is None
    snapshot["request"].pop("wait")
    snapshot["history"].append("|win|TestUser")
    assert BrowserSession().analyze(snapshot)["command"] is None


def test_browser_fails_closed_on_incomplete_history(snapshot):
    snapshot["history"] = ["|gametype|singles"]
    with pytest.raises(ValueError, match="both players"):
        BrowserSession().analyze(snapshot)


def test_browser_failed_replay_discards_state(snapshot, monkeypatch):
    def bad_replay(*_):
        raise RuntimeError("Broken protocol")

    monkeypatch.setattr("fp.run_battle.process_battle_updates", bad_replay)
    session = BrowserSession()
    with pytest.raises(ValueError, match="reconstruct"):
        session.analyze(snapshot)
    assert session.battle is None


def test_browser_rejects_doubles_and_wrong_format(snapshot):
    snapshot["history"][0] = "|gametype|doubles"
    with pytest.raises(ValueError, match="singles"):
        BrowserSession().analyze(snapshot)
    snapshot["pokemon_format"] = "gen8ou"
    with pytest.raises(ValueError, match="format"):
        BrowserSession().analyze(snapshot)


def test_old_decisions_are_cleared_between_requests_and_battles():
    for event in [
        "battle_started",
        "battle_attached",
        "battle_updated",
        "search_started",
        "battle_finished",
        "connection_lost",
    ]:
        store = EventStore()
        store.publish("decision_ready", result={"choice": "tackle"})
        store.publish(event)
        assert store.snapshot()["decision"] is None


def test_browser_http_endpoint_is_opt_in_and_serves_installable_userscript(
    monkeypatch, snapshot
):
    url = start_dashboard("127.0.0.1", 0)
    monkeypatch.setattr(
        FoulPlayConfig, "bot_mode", BotModes.search_ladder, raising=False
    )
    request = Request(
        url + "/api/browser",
        data=json.dumps(snapshot).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(url + "/foul-play.user.js", timeout=3) as response:
            assert response.headers.get_content_type() == "application/javascript"
            assert b"// ==UserScript==" in response.read()
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=3)
        assert error.value.code == 404
        monkeypatch.setattr(FoulPlayConfig, "bot_mode", BotModes.browser)
        monkeypatch.setattr(
            "fp.custom.browser.BROWSER_SESSION.analyze",
            lambda p: {
                "rqid": p["request"]["rqid"],
                "command": "/choose move tackle|1",
            },
        )
        with urlopen(request, timeout=3) as response:
            assert json.load(response)["command"] == "/choose move tackle|1"
    finally:
        stop_dashboard()
