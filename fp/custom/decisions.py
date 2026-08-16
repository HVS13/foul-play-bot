from copy import deepcopy
from dataclasses import dataclass

from fp import constants
from fp.battle.helpers import normalize_name
from fp.data import all_move_json
from fp.search.poke_engine_helpers import poke_engine_get_damage_rolls


HEALING_MOVES = {
    "recover",
    "roost",
    "softboiled",
    "wish",
    "moonlight",
    "morningsun",
    "synthesis",
    "slackoff",
    "milkdrink",
    "shoreup",
    "healorder",
    "rest",
}

PROTECT_MOVE_IDS = set(
    constants.PROTECT_VOLATILE_STATUSES
    + ["detect", "kingsshield", "obstruct", "silktrap"]
)


@dataclass(frozen=True)
class DecisionInfo:
    decision: str
    move_id: str | None
    tags: tuple[str, ...]
    can_ko: bool = False

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "move_id": self.move_id,
            "tags": list(self.tags),
            "can_ko": self.can_ko,
        }


@dataclass(frozen=True)
class SearchResult:
    choice: str
    policy: list[tuple[str, float]]
    confidence_ratio: float
    sampled_states: int
    search_passes: int
    search_time_per_state_ms: int
    total_search_time_ms: int
    risk_mode: str

    def to_dict(self, top: int = 5) -> dict:
        return {
            "choice": self.choice,
            "policy": [
                {
                    "move": move,
                    "weight": round(weight, 6),
                    "tags": list(analyze_decision(None, move).tags),
                }
                for move, weight in self.policy[:top]
            ],
            "confidence_ratio": (
                None
                if self.confidence_ratio == float("inf")
                else round(self.confidence_ratio, 4)
            ),
            "sampled_states": self.sampled_states,
            "search_passes": self.search_passes,
            "search_time_per_state_ms": self.search_time_per_state_ms,
            "total_search_time_ms": self.total_search_time_ms,
            "risk_mode": self.risk_mode,
        }


def _is_setup_move(move_json: dict) -> bool:
    return constants.BOOSTS in move_json or (
        constants.SELF in move_json and constants.BOOSTS in move_json[constants.SELF]
    )


def _move_can_ko(battle, move_id: str) -> bool:
    if (
        battle is None
        or battle.team_preview
        or battle.user.active is None
        or battle.opponent.active is None
    ):
        return False

    battle_copy = deepcopy(battle)
    if battle_copy.request_json is not None:
        battle_copy.user.update_from_request_json(battle_copy.request_json)
    try:
        damage_rolls, _ = poke_engine_get_damage_rolls(
            battle_copy, move_id, constants.DO_NOTHING_MOVE, True
        )
    except Exception:
        return False
    return bool(damage_rolls) and max(damage_rolls) >= battle_copy.opponent.active.hp


def analyze_decision(battle, decision: str, include_ko: bool = False) -> DecisionInfo:
    normalized_decision = decision.removesuffix("-tera").removesuffix("-mega")
    tags = []

    if normalized_decision.startswith(constants.SWITCH_STRING + " "):
        return DecisionInfo(decision=decision, move_id=None, tags=("switch",))

    move_id = normalize_name(normalized_decision)
    move_json = all_move_json.get(move_id)

    if move_id in constants.SWITCH_OUT_MOVES:
        tags.append("pivot")
    if move_id in PROTECT_MOVE_IDS:
        tags.append("protect")

    if move_json is None:
        return DecisionInfo(decision=decision, move_id=move_id, tags=tuple(tags))

    if move_json.get(constants.PRIORITY, 0) > 0:
        tags.append("priority")

    if move_id in HEALING_MOVES or move_json.get("heal"):
        tags.append("heal")
    elif _is_setup_move(move_json):
        tags.append("setup")
    elif move_json.get(constants.CATEGORY) == constants.MoveCategory.STATUS:
        tags.append("status")
    else:
        tags.append("attack")

    can_ko = include_ko and _move_can_ko(battle, move_id)
    if can_ko:
        tags.append("ko")

    return DecisionInfo(
        decision=decision,
        move_id=move_id,
        tags=tuple(dict.fromkeys(tags)),
        can_ko=can_ko,
    )
