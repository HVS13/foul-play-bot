import json
import logging
import os
import platform
import time
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version

from fp import constants
from fp.config import FoulPlayConfig, SaveReplay
from fp.custom.decisions import analyze_decision

logger = logging.getLogger(__name__)

LAST_BATTLE_TAG_PATH = os.path.join("logs", "last_battle_tag.txt")
DEFAULT_GUI_SUMMARY_JSON_PATH = os.path.join("logs", "battle_summary.jsonl")


def write_last_battle_tag(battle_tag: str | None) -> None:
    if not battle_tag:
        return
    try:
        os.makedirs(os.path.dirname(LAST_BATTLE_TAG_PATH), exist_ok=True)
        with open(LAST_BATTLE_TAG_PATH, "w", encoding="utf-8") as handle:
            handle.write(battle_tag)
    except OSError as exc:
        logger.warning("Failed to persist last battle tag: %s", exc)


def clear_last_battle_tag() -> None:
    try:
        os.remove(LAST_BATTLE_TAG_PATH)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Failed to clear last battle tag: %s", exc)


def battle_is_finished(battle_tag: str, msg: str) -> bool:
    return (
        msg.startswith(">{}".format(battle_tag))
        and (constants.WIN_STRING in msg or constants.TIE_STRING in msg)
        and constants.CHAT_STRING not in msg
    )


def extract_winner(msg: str) -> str | None:
    if constants.WIN_STRING in msg:
        return msg.split(constants.WIN_STRING)[-1].split("\n")[0].strip()
    return None


def extract_win_reason(msg: str) -> str | None:
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


def log_suggested_moves(
    battle, policy: list[tuple[str, float]], limit: int = 3
) -> None:
    if not policy:
        logger.info("Suggested moves: <none>")
        return

    logger.info("Suggested moves (top %s, ordered by policy weight):", limit)
    for move, weight in policy[:limit]:
        tags = analyze_decision(battle, move, include_ko=False).tags
        tag_string = " [{}]".format(", ".join(tags)) if tags else ""
        logger.info("\t{}%: {}{}".format(round(weight * 100, 3), move, tag_string))


def _policy_confidence(policy) -> float | None:
    if not policy or len(policy) < 2 or policy[1][1] <= 0:
        return None
    return round(policy[0][1] / policy[1][1], 4)


def record_decision(battle, decision: str, elapsed_ms: int, policy=None) -> dict:
    battle.search_times_ms.append(elapsed_ms)
    battle.decision_count += 1

    decision_info = analyze_decision(battle, decision, include_ko=True)
    entry = {
        "turn": battle.turn or 0,
        "rqid": battle.rqid,
        "time_remaining": battle.time_remaining,
        "decision": decision,
        "search_time_ms": elapsed_ms,
        "configured_risk_mode": FoulPlayConfig.risk_mode.name,
        "confidence_ratio": _policy_confidence(policy),
        "tags": list(decision_info.tags),
    }
    if policy:
        entry["policy_top"] = [
            {
                "move": move,
                "weight": round(weight, 6),
                "tags": list(analyze_decision(battle, move, include_ko=False).tags),
            }
            for move, weight in policy[:5]
        ]
    battle.decision_log.append(entry)
    return entry


def should_save_replay(winner: str | None) -> bool:
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


def _ensure_parent(path: str | None) -> None:
    parent = os.path.dirname(path) if path else ""
    if parent:
        os.makedirs(parent, exist_ok=True)


def _summary_json_path() -> str | None:
    configured = getattr(FoulPlayConfig, "summary_json_path", None)
    if configured:
        return configured
    if getattr(FoulPlayConfig, "gui", False):
        return DEFAULT_GUI_SUMMARY_JSON_PATH
    return None


def _package_version(package_name: str) -> str | None:
    try:
        return version(package_name)
    except PackageNotFoundError:
        return None


def _search_config_snapshot() -> dict:
    return {
        "search_time_ms": FoulPlayConfig.search_time_ms,
        "parallelism": FoulPlayConfig.parallelism,
        "search_threads": FoulPlayConfig.search_threads,
        "team_preview_search_time_ms": FoulPlayConfig.team_preview_search_time_ms,
        "team_preview_search_parallelism": FoulPlayConfig.team_preview_search_parallelism,
    }


def _runtime_snapshot() -> dict:
    return {
        "python": platform.python_version(),
        "poke_engine": _package_version("poke-engine"),
    }


def write_battle_summary(battle, winner: str | None, reconnect_count: int = 0) -> None:
    summary_json_path = _summary_json_path()
    if not FoulPlayConfig.summary_path and not summary_json_path:
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
        "schema_version": 2,
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
        "search_config": _search_config_snapshot(),
        "runtime": _runtime_snapshot(),
        "reconnect_count": reconnect_count,
        "room_rename_count": getattr(battle, "room_rename_count", 0),
        "replay_saved": battle.replay_saved,
        "replay_url": battle.replay_url,
        "opponent_tendencies": battle.opponent_tendencies,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    if battle.decision_log:
        summary["decision_log"] = battle.decision_log
    if battle.started_at:
        summary["duration_seconds"] = int(time.time() - battle.started_at)

    if FoulPlayConfig.summary_path:
        _ensure_parent(FoulPlayConfig.summary_path)
        lines = [
            "{}: {}".format(key, value)
            for key, value in summary.items()
            if key != "decision_log"
        ]
        with open(FoulPlayConfig.summary_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n\n")

    if summary_json_path:
        _ensure_parent(summary_json_path)
        with open(summary_json_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(summary) + "\n")