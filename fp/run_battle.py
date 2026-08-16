import json
import logging
import time

from fp import constants
from fp.battle.helpers import normalize_name
from fp.battle.protocol import async_update_battle, process_battle_updates
from fp.battle.state import Battle
from fp.config import FoulPlayConfig
from fp.constants import BattleType
from fp.custom_features import (
    battle_is_finished,
    clear_last_battle_tag,
    extract_win_reason,
    extract_winner,
    should_save_replay,
    update_opponent_tendencies,
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


async def start_battle(ps_websocket_client, pokemon_battle_type, team_dict):
    format_spec = FormatSpec.from_format_string(pokemon_battle_type)
    battle = await battle_mode(format_spec.battle_type).start_battle(
        ps_websocket_client, pokemon_battle_type, team_dict
    )
    battle.started_at = battle.started_at or time.time()
    write_last_battle_tag(battle.battle_tag)

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
    clear_last_battle_tag()
    await ps_websocket_client.leave_battle(battle.battle_tag)
    return winner


async def run_battle_loop(ps_websocket_client, battle):
    while True:
        msg = await ps_websocket_client.receive_message()
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
        if action_required and not battle.wait:
            best_move = await async_pick_move(battle)
            await ps_websocket_client.send_message(battle.battle_tag, best_move)


def _extract_request_json(msg_lines):
    for line in msg_lines:
        split_line = line.split("|")
        if (
            len(split_line) >= 3
            and split_line[1].strip() == "request"
            and split_line[2].strip()
        ):
            return json.loads(split_line[2].strip("'"))
    return None


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


async def _resume_battle_state(ps_websocket_client, pokemon_battle_type, battle_tag):
    if not battle_tag:
        raise ValueError("battle_tag is required to resume a battle")

    write_last_battle_tag(battle_tag)
    await ps_websocket_client.join_room(battle_tag)
    backlog_msgs = []
    player_map = {}
    known_names = set()
    request_json = None

    while True:
        msg = await ps_websocket_client.receive_message()
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
        request_json = request_json or _extract_request_json(msg_lines)
        if request_json is not None and len(player_map) >= 2:
            break

    for pokemon_dict in request_json.get(constants.SIDE, {}).get(constants.POKEMON, []):
        name = normalize_name(pokemon_dict[constants.DETAILS].split(",")[0])
        if name:
            known_names.add(name)

    format_spec = FormatSpec.from_format_string(pokemon_battle_type)
    battle = Battle(battle_tag)
    battle.started_at = time.time()
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

    # Rebuild field/opponent history, then refresh the user's current state from
    # the latest request JSON. This keeps resume compatible with the upstream
    # protocol parser while avoiding stale user HP/move data.
    history_lines = []
    for backlog in backlog_msgs:
        for line in backlog.split("\n"):
            if "|request|" not in line and not line.startswith(">"):
                history_lines.append(line)
    battle.msg_list = history_lines
    try:
        process_battle_updates(battle)
    except Exception as exc:
        logger.warning("Partial history replay while resuming battle: %s", exc)
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
        resumed_battle, finished = await _resume_battle_state(
            ps_websocket_client, pokemon_battle_type, battle.battle_tag
        )
        if finished is not None:
            battle.win_reason = battle.win_reason or finished.get("win_reason")
            write_battle_summary(
                battle, finished.get("winner"), ps_websocket_client.reconnect_count
            )
            return finished.get("winner")
        battle = resumed_battle


async def resume_battle(ps_websocket_client, pokemon_battle_type, battle_tag):
    battle, finished = await _resume_battle_state(
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
