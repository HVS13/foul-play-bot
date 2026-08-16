import json
import logging
import os
import time
from copy import deepcopy
from datetime import datetime, timezone

from fp import constants
from fp.battle.helpers import normalize_name
from fp.config import FoulPlayConfig, SaveReplay
from fp.data import all_move_json
from fp.search.poke_engine_helpers import poke_engine_get_damage_rolls

logger = logging.getLogger(__name__)

LAST_BATTLE_TAG_PATH = os.path.join("logs", "last_battle_tag.txt")
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
_PROTECT_MOVE_IDS = set(
    constants.PROTECT_VOLATILE_STATUSES
    + ["detect", "kingsshield", "obstruct", "silktrap"]
)


def write_last_battle_tag(battle_tag):
    if not battle_tag:
        return
    try:
        os.makedirs(os.path.dirname(LAST_BATTLE_TAG_PATH), exist_ok=True)
        with open(LAST_BATTLE_TAG_PATH, "w", encoding="utf-8") as handle:
            handle.write(battle_tag)
    except OSError as exc:
        logger.warning("Failed to persist last battle tag: %s", exc)


def clear_last_battle_tag():
    try:
        os.remove(LAST_BATTLE_TAG_PATH)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Failed to clear last battle tag: %s", exc)


def battle_is_finished(battle_tag, msg):
    return (
        msg.startswith(">{}".format(battle_tag))
        and (constants.WIN_STRING in msg or constants.TIE_STRING in msg)
        and constants.CHAT_STRING not in msg
    )


def extract_winner(msg):
    if constants.WIN_STRING in msg:
        return msg.split(constants.WIN_STRING)[-1].split("\n")[0].strip()
    return None


def extract_win_reason(msg):
    reason = None
    for line in msg.split("\n"):
        if not line.startswith("|"):
            continue
        action = line.split("|")[1].strip() if "|" in line else ""
        lower = line.lower()
        if action == "forfeit" or "|forfeit|" in line:
            return "forfeit"
        if "timeout" in lower:
            reason = reason or "timeout"
        elif "disconnect" in lower:
            reason = reason or "disconnect"
    return reason


def update_opponent_tendencies(battle, msg):
    if not getattr(battle.opponent, "name", None):
        return
    tendencies = battle.opponent_tendencies
    for line in msg.split("\n"):
        if not line.startswith("|"):
            continue
        split_line = line.split("|")
        if len(split_line) < 3:
            continue
        action = split_line[1].strip()
        actor = split_line[2].strip()
        if not actor.startswith(battle.opponent.name):
            continue
        if action in {"switch", "drag", "replace"}:
            tendencies["switches"] += 1
            tendencies["actions"] += 1
        elif action == "move":
            tendencies["moves"] += 1
            tendencies["actions"] += 1
            if (
                len(split_line) > 3
                and normalize_name(split_line[3]) in _PROTECT_MOVE_IDS
            ):
                tendencies["protects"] += 1


def _is_setup_move(move_json):
    return constants.BOOSTS in move_json or (
        constants.SELF in move_json and constants.BOOSTS in move_json[constants.SELF]
    )


def _move_can_ko(battle, move_id):
    if (
        battle.team_preview
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


def move_reason_tags(battle, decision):
    tags = []
    decision = decision.removesuffix("-tera").removesuffix("-mega")
    if decision.startswith(constants.SWITCH_STRING + " "):
        return ["switch"]

    move_id = normalize_name(decision)
    if move_id in constants.SWITCH_OUT_MOVES:
        tags.append("pivot")
    move_json = all_move_json.get(move_id)
    if move_json is None:
        return tags
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
        if _move_can_ko(battle, move_id):
            tags.append("ko")
    return tags


def log_suggested_moves(battle, policy, limit=3):
    if not policy:
        logger.info("Suggested moves: <none>")
        return
    logger.info("Suggested moves (top %s, ordered by policy weight):", limit)
    for move, weight in policy[:limit]:
        tags = move_reason_tags(battle, move)
        tag_string = " [{}]".format(", ".join(tags)) if tags else ""
        logger.info("\t{}%: {}{}".format(round(weight * 100, 3), move, tag_string))


def record_decision(battle, decision, elapsed_ms, policy=None):
    battle.search_times_ms.append(elapsed_ms)
    battle.decision_count += 1
    entry = {
        "turn": battle.turn or 0,
        "decision": decision,
        "search_time_ms": elapsed_ms,
        "tags": move_reason_tags(battle, decision),
    }
    if policy:
        entry["policy_top"] = [
            {
                "move": move,
                "weight": round(weight, 6),
                "tags": move_reason_tags(battle, move),
            }
            for move, weight in policy[:3]
        ]
    battle.decision_log.append(entry)


def should_save_replay(winner):
    return (
        FoulPlayConfig.save_replay == SaveReplay.always
        or (
            FoulPlayConfig.save_replay == SaveReplay.on_loss
            and winner != FoulPlayConfig.username
        )
        or (
            FoulPlayConfig.save_replay == SaveReplay.on_win
            and winner == FoulPlayConfig.username
        )
    )


def write_battle_summary(battle, winner, reconnect_count=0):
    if not FoulPlayConfig.summary_path and not FoulPlayConfig.summary_json_path:
        return

    search_times = list(battle.search_times_ms or [])
    decision_count = battle.decision_count or len(search_times)
    search_summary = {}
    if search_times:
        total_ms = int(sum(search_times))
        search_summary = {
            "total": total_ms,
            "avg": round(total_ms / max(decision_count, 1), 2),
            "max": int(max(search_times)),
        }
    summary = {
        "battle_tag": battle.battle_tag,
        "format": battle.pokemon_format,
        "winner": winner,
        "win_reason": battle.win_reason,
        "turns": battle.turn or 0,
        "bot_mode": FoulPlayConfig.bot_mode.name,
        "risk_mode": FoulPlayConfig.risk_mode.name,
        "suggest_only": FoulPlayConfig.suggest_only,
        "decision_count": decision_count,
        "search_time_ms": search_summary,
        "reconnect_count": reconnect_count,
        "replay_saved": battle.replay_saved,
        "replay_url": battle.replay_url,
        "opponent_tendencies": battle.opponent_tendencies,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    if battle.decision_log:
        summary["decision_log"] = battle.decision_log
    if battle.started_at:
        summary["duration_seconds"] = int(time.time() - battle.started_at)

    def ensure_parent(path):
        parent = os.path.dirname(path) if path else ""
        if parent:
            os.makedirs(parent, exist_ok=True)

    if FoulPlayConfig.summary_path:
        ensure_parent(FoulPlayConfig.summary_path)
        lines = [
            "{}: {}".format(key, value)
            for key, value in summary.items()
            if key != "decision_log"
        ]
        with open(FoulPlayConfig.summary_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n\n")

    if FoulPlayConfig.summary_json_path:
        ensure_parent(FoulPlayConfig.summary_json_path)
        with open(FoulPlayConfig.summary_json_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(summary) + "\n")
