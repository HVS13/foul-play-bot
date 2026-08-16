import copy
import threading
from collections import deque
from datetime import datetime, timezone


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pokemon_snapshot(pokemon) -> dict | None:
    if pokemon is None:
        return None
    max_hp = getattr(pokemon, "max_hp", 0) or 0
    hp = getattr(pokemon, "hp", 0) or 0
    return {
        "name": getattr(pokemon, "name", None),
        "nickname": getattr(pokemon, "nickname", None),
        "hp": hp,
        "max_hp": max_hp,
        "hp_pct": round(hp / max_hp, 4) if max_hp else None,
        "status": str(getattr(pokemon, "status", None) or ""),
        "item": getattr(pokemon, "item", None),
    }


def _battle_snapshot(battle) -> dict:
    return {
        "battle_tag": getattr(battle, "battle_tag", None),
        "format": getattr(battle, "pokemon_format", None),
        "turn": getattr(battle, "turn", 0) or 0,
        "time_remaining": getattr(battle, "time_remaining", None),
        "wait": bool(getattr(battle, "wait", False)),
        "force_switch": bool(getattr(battle, "force_switch", False)),
        "user": {
            "name": getattr(getattr(battle, "user", None), "name", None),
            "active": _pokemon_snapshot(
                getattr(getattr(battle, "user", None), "active", None)
            ),
        },
        "opponent": {
            "name": getattr(getattr(battle, "opponent", None), "account_name", None)
            or getattr(getattr(battle, "opponent", None), "name", None),
            "active": _pokemon_snapshot(
                getattr(getattr(battle, "opponent", None), "active", None)
            ),
        },
        "opponent_tendencies": dict(
            getattr(battle, "opponent_tendencies", {}) or {}
        ),
    }


class EventStore:
    def __init__(self, max_events: int = 30):
        self._lock = threading.Lock()
        self._events = deque(maxlen=max_events)
        self._state = {
            "status": "idle",
            "connection": "disconnected",
            "battle": None,
            "decision": None,
            "updated_at": _utc_now(),
            "event_id": 0,
        }

    def publish(self, event_type: str, battle=None, **payload) -> None:
        with self._lock:
            event_id = self._state["event_id"] + 1
            event = {
                "id": event_id,
                "type": event_type,
                "timestamp": _utc_now(),
                "data": copy.deepcopy(payload),
            }
            self._events.append(event)
            self._state["event_id"] = event_id
            self._state["updated_at"] = event["timestamp"]

            if battle is not None:
                self._state["battle"] = _battle_snapshot(battle)

            if event_type in {"connection_open", "reconnected"}:
                self._state["connection"] = "connected"
            elif event_type == "connection_lost":
                self._state["connection"] = "reconnecting"
            elif event_type == "connection_closed":
                self._state["connection"] = "disconnected"

            if event_type in {"battle_started", "battle_attached", "battle_updated"}:
                self._state["status"] = "battle"
            elif event_type == "search_started":
                self._state["status"] = "searching"
            elif event_type == "decision_ready":
                self._state["status"] = "decision_ready"
                self._state["decision"] = copy.deepcopy(payload)
            elif event_type == "battle_finished":
                self._state["status"] = "finished"
            elif event_type == "idle":
                self._state["status"] = "idle"

    def snapshot(self) -> dict:
        with self._lock:
            snapshot = copy.deepcopy(self._state)
            snapshot["events"] = copy.deepcopy(list(self._events))
            return snapshot


EVENT_STORE = EventStore()


def publish_event(event_type: str, battle=None, **payload) -> None:
    EVENT_STORE.publish(event_type, battle=battle, **payload)


def event_snapshot() -> dict:
    return EVENT_STORE.snapshot()
