"""Read browser battle snapshots; return recommendations without a Showdown login."""

import asyncio
import json
import re
import threading

from fp.battle.protocol import update_battle
from fp.config import FoulPlayConfig
from fp.custom.events import event_snapshot, publish_event
from fp.custom.opponent_model import update_opponent_tendencies
from fp.modes.base import async_pick_move
from fp.run_battle import attach_to_battle


class SnapshotClient:
    """The attach path reads this snapshot, never a second websocket session."""

    def __init__(self, message):
        self.message = message
        self.commands = []

    async def join_room(self, room):
        pass

    async def receive_message(self):
        if self.message is None:
            raise ValueError("Snapshot needs both players and a private request")
        message, self.message = self.message, None
        return message

    async def send_message(self, room, messages):
        self.commands.append("|".join(messages))

    @staticmethod
    def parse_room_rename(message):
        return None


class BrowserSession:
    def __init__(self):
        self.lock = threading.Lock()
        self.battle = None
        self.history = []
        self.identity = None
        self.last_key = None
        self.last_response = None

    def analyze(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("Expected a battle snapshot")
        room = payload.get("battle_tag", "")
        if not isinstance(room, str) or not re.fullmatch(r"battle-[a-z0-9-]+", room):
            raise ValueError("Invalid battle room")
        if payload.get("pokemon_format") != FoulPlayConfig.pokemon_format:
            raise ValueError("Battle format must match --pokemon-format")
        request = payload.get("request")
        if not isinstance(request, dict) or not isinstance(request.get("rqid"), int):
            raise ValueError("A current private request with rqid is required")
        side = request.get("side", {})
        if side.get("id") not in {"p1", "p2"} or not side.get("name"):
            raise ValueError("Join as a player to get recommendations")
        history = payload.get("history")
        if not isinstance(history, list) or not all(
            isinstance(x, str) for x in history
        ):
            raise ValueError("Battle history is required")
        game_types = [x for x in history if x.startswith("|gametype|")]
        if game_types != ["|gametype|singles"]:
            raise ValueError("Only singles battles are supported")
        if request.get("wait") or any(x.startswith(("|win|", "|tie")) for x in history):
            return {"battle_tag": room, "rqid": request["rqid"], "command": None}
        if not self.lock.acquire(blocking=False):
            raise BlockingIOError("Engine is busy; try again shortly")
        try:
            return asyncio.run(self._analyze(payload))
        except Exception:
            # A failed replay must never leave a partially updated state in use.
            self.battle = None
            self.last_key = None
            publish_event("idle")
            raise
        finally:
            self.lock.release()

    async def _analyze(self, payload):
        room, history, request = (
            payload["battle_tag"],
            payload["history"],
            payload["request"],
        )
        identity = (payload.get("client_id"), room, request["side"]["name"])
        key = (identity, json.dumps(request, sort_keys=True), tuple(history))
        if key == self.last_key:
            return self.last_response
        FoulPlayConfig.username = request["side"]["name"]
        FoulPlayConfig.user_id = FoulPlayConfig.username
        client = SnapshotClient(
            ">{}\n{}\n|request|{}".format(room, "\n".join(history), json.dumps(request))
        )
        if (
            self.battle is None
            or identity != self.identity
            or history[: len(self.history)] != self.history
        ):
            self.battle, finished = await attach_to_battle(
                client,
                FoulPlayConfig.pokemon_format,
                room,
                choose_move=False,
                strict_history=True,
            )
            if finished is not None:
                raise ValueError("Battle has already finished")
        else:
            delta = "\n".join(history[len(self.history) :])
            update_opponent_tendencies(self.battle, delta)
            update_battle(self.battle, delta + "\n|request|" + json.dumps(request))
            self.battle.team_preview = bool(request.get("teamPreview"))
        self.identity = identity
        self.history = list(history)
        timer = payload.get("time_remaining")
        if isinstance(timer, (int, float)) and not isinstance(timer, bool):
            self.battle.time_remaining = max(0, timer)
        publish_event("connection_open", source="browser")
        publish_event("battle_updated", self.battle)
        if self.battle.team_preview:
            await self.battle.mode.handle_team_preview(self.battle, client)
        else:
            command = await async_pick_move(self.battle)
            await client.send_message(room, command)
        response = {
            "battle_tag": room,
            "rqid": request["rqid"],
            "command": client.commands[-1],
            "state": event_snapshot(),
        }
        self.last_key, self.last_response = key, response
        return response


BROWSER_SESSION = BrowserSession()
