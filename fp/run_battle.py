import json
import logging
import time

from fp import constants
from fp.battle.helpers import normalize_name
from fp.battle.protocol import async_update_battle, process_battle_updates
from fp.battle.state import Battle
from fp.config import FoulPlayConfig
from fp.constants import BattleType
from fp.custom.events import publish_event
from fp.custom.opponent_model import update_opponent_tendencies
from fp.custom.telemetry import (
    battle_is_finished,
    clear_last_battle_tag,
    extract_win_reason,
    extract_winner,
    should_save_replay,
    write_battle_summary,
    write_last_battle_tag,
)
from fp.format_spec import FormatSpec
from fp.modes import battle_mode
from fp.modes.base import async_pick_move

logger = logging.getLogger(__name__)
RECONNECT_RESUME = object()


def _message_indicates_battle_end(battle_tag, msg):
    return battle_is_finished(battle_tag, msg) or (
        msg.startswith(">{}".format(battle_tag)) and "|deinit|" in msg
    )


def _apply_room_rename(ps_websocket_client, battle, msg):
    rename = ps_websocket_client.parse_room_rename(msg)
    if rename is None:
        return False

    old_room, new_room = rename
    current_room = battle.battle_tag
    if current_room not in {old_room, new_room} and (
        ps_websocket_client.resolve_room(current_room) != new_room
    ):
        return False
    if current_room == new_room:
        return False

    battle.battle_tag = new_room
    battle.room_rename_count = getattr(battle, "room_rename_count", 0) + 1
    write_last_battle_tag(new_room)
    publish_event(
        "battle_room_renamed",
        battle,
        old_room=old_room,
        new_room=new_room,
        room_rename_count=battle.room_rename_count,
    )
    logger.info("Battle state moved to renamed room: %s -> %s", old_room, new_room)
    return True


async def start_battle(ps_websocket_client, pokemon_battle_type, team_dict):
    format_spec = FormatSpec.from_format_string(pokemon_battle_type)
    battle = await battle_mode(format_spec.battle_type).start_battle(
        ps_websocket_client, pokemon_battle_type, team_dict
    )
    battle.started_at = battle.started_at or time.time()
    write_last_battle_tag(battle.battle_tag)
    publish_event("battle_started", battle)

    await ps_websocket_client.send_message(battle.battle_tag, ["hf"])
    if FoulPlayConfig.battle_timer != "none":
        await ps_websocket_client.send_message(
            battle.battle_tag, ["/timer {}".format(FoulPlayConfig.battle_timer)]
        )
    return battle


async def _finish_battle(ps_websocket_client, battle, msg):
    winner = extract_winner(msg)
    battle.win_reason = battle.win_reason or extract_win_reason(msg)
    if winner is None:
        battle.win_reason = battle.win_reason or "tie"
    else:
        battle.win_reason = battle.win_reason or "normal"
    logger.info("Winner: {}".format(winner))

    await ps_websocket_client.send_message(battle.battle_tag, ["gg"])
    if should_save_replay(winner):
        await ps_websocket_client.save_replay(battle.battle_tag)
        battle.replay_saved = True
        battle.replay_url = "https://replay.pokemonshowdown.com/{}".format(
            battle.battle_tag
        )

    write_battle_summary(battle, winner, ps_websocket_client.reconnect_count)
    publish_event(
        "battle_finished",
        battle,
        winner=winner,
        win_reason=battle.win_reason,
    )
    clear_last_battle_tag()
    await ps_websocket_client.leave_battle(battle.battle_tag)
    return winner


async def run_battle_loop(ps_websocket_client, battle):
    while True:
        msg = await ps_websocket_client.receive_message()
        _apply_room_rename(ps_websocket_client, battle, msg)
        if battle.win_reason is None:
            battle.win_reason = extract_win_reason(msg)
        update_opponent_tendencies(battle, msg)

        if _message_indicates_battle_end(battle.battle_tag, msg):
            return await _finish_battle(ps_websocket_client, battle, msg)

        if ps_websocket_client.consume_reconnect_flag():
            logger.warning(
                "Websocket reconnected during battle %s; rebuilding battle state",
                battle.battle_tag,
            )
            return RECONNECT_RESUME

        action_required = await async_update_battle(battle, msg)
        publish_event("battle_updated", battle)
        if action_required and not battle.wait:
            best_move = await async_pick_move(battle)
            await ps_websocket_client.send_message(battle.battle_tag, best_move)


def _request_id(request_json):
    if request_json is None:
        return -1
    try:
        return int(request_json.get(constants.RQID, -1))
    except (TypeError, ValueError):
        return -1


def _extract_request_json(msg_lines):
    latest = None
    for line in msg_lines:
        split_line = line.split("|")
        if (
            len(split_line) >= 3
            and split_line[1].strip() == "request"
            and split_line[2].strip()
        ):
            candidate = json.loads(split_line[2].strip("'"))
            if latest is None or _request_id(candidate) >= _request_id(latest):
                latest = candidate
    return latest


def _collect_player_map(msg_lines, player_map):
    for line in msg_lines:
        if line.startswith("|player|"):
            split_line = line.split("|")
            if len(split_line) >= 4:
                player_map[split_line[2]] = split_line[3]


def _collect_known_pokemon(msg_lines, known_names):
    for line in msg_lines:
        split_line = line.split("|")
        if len(split_line) < 4:
            continue
        if split_line[1].strip() in {
            "poke",
            "switch",
            "drag",
            "replace",
            "detailschange",
        }:
            name = normalize_name(split_line[3].split(",")[0])
            if name:
                known_names.add(name)


def _resolve_player_sides(player_map):
    candidates = {
        normalize_name(FoulPlayConfig.user_id or FoulPlayConfig.username),
        normalize_name(FoulPlayConfig.username),
    }
    for side_id, account in player_map.items():
        if normalize_name(account) in candidates:
            opponent_side = constants.ID_LOOKUP.get(side_id)
            return side_id, opponent_side, player_map.get(opponent_side)
    return None, None, None


def _initialize_resume_datasets(battle, known_names, backlog_text):
    mode = battle.mode
    if battle.battle_type == BattleType.RANDOM_BATTLE:
        mode.datasets.initialize(battle.format_spec)
        return
    if known_names and hasattr(mode, "initialize_team_preview_datasets"):
        try:
            mode.initialize_team_preview_datasets(
                battle.pokemon_format, known_names, backlog_text
            )
        except Exception as exc:
            logger.warning("Could not fully initialize resume datasets: %s", exc)


def _carry_session_metadata(battle, previous_battle):
    if previous_battle is None:
        battle.started_at = time.time()
        battle.room_rename_count = 0
        return

    battle.started_at = previous_battle.started_at or time.time()
    battle.search_times_ms = list(previous_battle.search_times_ms)
    battle.decision_log = list(previous_battle.decision_log)
    battle.decision_count = previous_battle.decision_count
    battle.win_reason = previous_battle.win_reason
    battle.replay_url = previous_battle.replay_url
    battle.replay_saved = previous_battle.replay_saved
    battle.room_rename_count = getattr(previous_battle, "room_rename_count", 0)
    battle.opponent_tendencies = dict(previous_battle.opponent_tendencies)


async def attach_to_battle(
    ps_websocket_client,
    pokemon_battle_type,
    battle_tag,
    previous_battle=None,
):
    if not battle_tag:
        raise ValueError("battle_tag is required to attach to a battle")

    write_last_battle_tag(battle_tag)
    await ps_websocket_client.join_room(battle_tag)
    backlog_msgs = []
    player_map = {}
    known_names = set()
    request_json = None
    attach_room_renames = 0

    while True:
        msg = await ps_websocket_client.receive_message()
        rename = ps_websocket_client.parse_room_rename(msg)
        if rename is not None:
            old_room, new_room = rename
            if (
                battle_tag == old_room
                or ps_websocket_client.resolve_room(battle_tag) == new_room
            ):
                if battle_tag != new_room:
                    battle_tag = new_room
                    attach_room_renames += 1
                    write_last_battle_tag(battle_tag)
                continue

        msg_lines = msg.split("\n")
        first = msg_lines[0].strip() if msg_lines else ""
        if not first.startswith(">{}".format(battle_tag)):
            continue
        if _message_indicates_battle_end(battle_tag, msg):
            clear_last_battle_tag()
            return None, {
                "winner": extract_winner(msg),
                "win_reason": extract_win_reason(msg)
                or ("tie" if constants.TIE_STRING in msg else "normal"),
            }

        backlog_msgs.append(msg)
        _collect_player_map(msg_lines, player_map)
        _collect_known_pokemon(msg_lines, known_names)
        candidate_request = _extract_request_json(msg_lines)
        if candidate_request is not None and (
            request_json is None
            or _request_id(candidate_request) >= _request_id(request_json)
        ):
            request_json = candidate_request
        if request_json is not None and len(player_map) >= 2:
            break

    for pokemon_dict in request_json.get(constants.SIDE, {}).get(constants.POKEMON, []):
        name = normalize_name(pokemon_dict[constants.DETAILS].split(",")[0])
        if name:
            known_names.add(name)

    format_spec = FormatSpec.from_format_string(pokemon_battle_type)
    battle = Battle(battle_tag)
    _carry_session_metadata(battle, previous_battle)
    battle.room_rename_count += attach_room_renames
    battle.pokemon_format = pokemon_battle_type
    battle.generation = format_spec.generation
    battle.battle_type = format_spec.battle_type
    battle.mode = battle_mode(format_spec.battle_type)

    user_side, opponent_side, opponent_account = _resolve_player_sides(player_map)
    if user_side is None or opponent_side is None:
        raise ValueError(
            "Could not match logged-in user to battle players: {}".format(player_map)
        )
    battle.user.name = user_side
    battle.opponent.name = opponent_side
    battle.opponent.account_name = opponent_account

    if FoulPlayConfig.log_to_file:
        FoulPlayConfig.file_log_handler.do_rollover(
            "{}_{}.log".format(battle_tag, opponent_account or "unknown")
        )

    battle.request_json = request_json
    battle.user.initialize_first_turn_user_from_json(request_json)
    battle.rqid = request_json.get(constants.RQID)
    _initialize_resume_datasets(battle, known_names, "\n".join(backlog_msgs))

    history_lines = []
    for backlog in backlog_msgs:
        for line in backlog.split("\n"):
            if "|request|" not in line and not line.startswith(">"):
                history_lines.append(line)
    battle.msg_list = history_lines
    try:
        process_battle_updates(battle)
    except Exception as exc:
        logger.warning("Partial history replay while attaching to battle: %s", exc)
        battle.msg_list.clear()
    try:
        battle.user.update_from_request_json(request_json)
    except Exception:
        battle.user.initialize_first_turn_user_from_json(request_json)
    battle.started = True
    battle.request_json = request_json
    battle.rqid = request_json.get(constants.RQID)
    battle.force_switch = bool(request_json.get(constants.FORCE_SWITCH))
    battle.wait = bool(request_json.get(constants.WAIT))

    if previous_battle is None:
        for backlog in backlog_msgs:
            update_opponent_tendencies(battle, backlog)

    publish_event(
        "battle_attached",
        battle,
        resumed=previous_battle is not None,
        rqid=battle.rqid,
    )

    if FoulPlayConfig.battle_timer != "none":
        await ps_websocket_client.send_message(
            battle.battle_tag, ["/timer {}".format(FoulPlayConfig.battle_timer)]
        )
    if not battle.wait:
        best_move = await async_pick_move(battle)
        await ps_websocket_client.send_message(battle.battle_tag, best_move)
    return battle, None


async def _run_battle_loop_with_auto_resume(
    ps_websocket_client, battle, pokemon_battle_type
):
    while True:
        result = await run_battle_loop(ps_websocket_client, battle)
        if result is not RECONNECT_RESUME:
            return result
        resumed_battle, finished = await attach_to_battle(
            ps_websocket_client,
            pokemon_battle_type,
            battle.battle_tag,
            previous_battle=battle,
        )
        if finished is not None:
            battle.win_reason = battle.win_reason or finished.get("win_reason")
            write_battle_summary(
                battle, finished.get("winner"), ps_websocket_client.reconnect_count
            )
            publish_event(
                "battle_finished",
                battle,
                winner=finished.get("winner"),
                win_reason=battle.win_reason,
            )
            clear_last_battle_tag()
            return finished.get("winner")
        battle = resumed_battle


async def resume_battle(ps_websocket_client, pokemon_battle_type, battle_tag):
    battle, finished = await attach_to_battle(
        ps_websocket_client, pokemon_battle_type, battle_tag
    )
    if finished is not None:
        return finished.get("winner")
    return await _run_battle_loop_with_auto_resume(
        ps_websocket_client, battle, pokemon_battle_type
    )


async def pokemon_battle(ps_websocket_client, pokemon_battle_type, team_dict):
    battle = await start_battle(ps_websocket_client, pokemon_battle_type, team_dict)
    return await _run_battle_loop_with_auto_resume(
        ps_websocket_client, battle, pokemon_battle_type
    )
