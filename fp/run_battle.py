import json
import asyncio
import concurrent.futures
import os
from copy import deepcopy
from datetime import datetime
import logging
import time

from data.pkmn_sets import RandomBattleTeamDatasets, TeamDatasets
from data.pkmn_sets import SmogonSets
from data import all_move_json
import constants
from constants import BattleType
from config import FoulPlayConfig, SaveReplay
from fp.battle import LastUsedMove, Pokemon, Battle
from fp.battle_modifier import async_update_battle, process_battle_updates, update_battle
from fp.helpers import normalize_name
from fp.search.main import find_best_move, find_best_move_with_policy
from fp.search.poke_engine_helpers import poke_engine_get_damage_rolls

from fp.websocket_client import PSWebsocketClient

logger = logging.getLogger(__name__)

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

LAST_BATTLE_TAG_PATH = os.path.join("logs", "last_battle_tag.txt")
RECONNECT_RESUME = object()


def _write_last_battle_tag(battle_tag):
    if not battle_tag:
        return
    try:
        os.makedirs(os.path.dirname(LAST_BATTLE_TAG_PATH), exist_ok=True)
        with open(LAST_BATTLE_TAG_PATH, "w", encoding="utf-8") as handle:
            handle.write(battle_tag)
    except Exception as exc:
        logger.warning("Failed to persist last battle tag: %s", exc)


def _clear_last_battle_tag():
    try:
        os.remove(LAST_BATTLE_TAG_PATH)
    except FileNotFoundError:
        return
    except Exception as exc:
        logger.warning("Failed to clear last battle tag: %s", exc)


def _is_setup_move(move_json):
    if constants.BOOSTS in move_json:
        return True
    if (
        constants.SELF in move_json
        and constants.BOOSTS in move_json[constants.SELF]
    ):
        return True
    return False


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
            battle_copy,
            move_id,
            constants.DO_NOTHING_MOVE,
            True,
        )
    except Exception:
        return False
    if not damage_rolls:
        return False
    return max(damage_rolls) >= battle_copy.opponent.active.hp


def _move_reason_tags(battle, decision):
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
    elif move_json.get(constants.CATEGORY) == constants.STATUS:
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

    logger.info(
        "Suggested moves (top {}, ordered by policy weight):".format(limit)
    )
    for move, weight in policy[:limit]:
        tags = _move_reason_tags(battle, move)
        tag_string = " [{}]".format(", ".join(tags)) if tags else ""
        logger.info(
            "\t{}%: {}{}".format(round(weight * 100, 3), move, tag_string)
        )


def format_decision(battle, decision):
    # Formats a decision for communication with Pokemon-Showdown
    # If the move can be used as a Z-Move, it will be

    if decision.startswith(constants.SWITCH_STRING + " "):
        switch_pokemon = decision.split("switch ")[-1]
        for pkmn in battle.user.reserve:
            if pkmn.name == switch_pokemon:
                message = "/switch {}".format(pkmn.index)
                break
        else:
            raise ValueError("Tried to switch to: {}".format(switch_pokemon))
    else:
        tera = False
        mega = False
        if decision.endswith("-tera"):
            decision = decision.replace("-tera", "")
            tera = True
        elif decision.endswith("-mega"):
            decision = decision.replace("-mega", "")
            mega = True
        message = "/choose move {}".format(decision)

        if battle.user.active.can_mega_evo and mega:
            message = "{} {}".format(message, constants.MEGA)
        elif battle.user.active.can_ultra_burst:
            message = "{} {}".format(message, constants.ULTRA_BURST)

        # only dynamax on last pokemon
        if battle.user.active.can_dynamax and all(
            p.hp == 0 for p in battle.user.reserve
        ):
            message = "{} {}".format(message, constants.DYNAMAX)

        if tera:
            message = "{} {}".format(message, constants.TERASTALLIZE)

        if battle.user.active.get_move(decision).can_z:
            message = "{} {}".format(message, constants.ZMOVE)

    return [message, str(battle.rqid)]


def battle_is_finished(battle_tag, msg):
    return (
        msg.startswith(">{}".format(battle_tag))
        and (constants.WIN_STRING in msg or constants.TIE_STRING in msg)
        and constants.CHAT_STRING not in msg
    )


def extract_battle_factory_tier_from_msg(msg):
    start = msg.find("Battle Factory Tier: ") + len("Battle Factory Tier: ")
    end = msg.find("</b>", start)
    tier_name = msg[start:end]

    return normalize_name(tier_name)


def _extract_win_reason(msg):
    reason = None
    for line in msg.split("\n"):
        if not line.startswith("|"):
            continue
        split_line = line.split("|")
        if len(split_line) < 2:
            continue
        action = split_line[1].strip()
        if action == "c":
            continue
        lower = line.lower()
        if action == "forfeit" or "|forfeit|" in line:
            return "forfeit"
        if "timeout" in lower:
            reason = reason or "timeout"
        elif "disconnect" in lower or "disconnected" in lower:
            reason = reason or "disconnect"
    return reason


def _extract_winner_from_msg(msg):
    if constants.WIN_STRING in msg:
        return msg.split(constants.WIN_STRING)[-1].split("\n")[0].strip()
    return None


def _message_indicates_battle_end(battle_tag, msg):
    if battle_is_finished(battle_tag, msg):
        return True
    if msg.startswith(">{}".format(battle_tag)) and "|deinit|" in msg:
        return True
    return False


_PROTECT_MOVE_IDS = set(
    constants.PROTECT_VOLATILE_STATUSES
    + ["detect", "kingsshield", "obstruct", "silktrap"]
)


def _update_opponent_tendencies(battle, msg):
    if not battle.opponent.name:
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
            continue

        if action == "move":
            tendencies["moves"] += 1
            tendencies["actions"] += 1
            if len(split_line) > 3:
                move_id = normalize_name(split_line[3])
                if move_id in _PROTECT_MOVE_IDS:
                    tendencies["protects"] += 1


def _extract_battle_room_id(msg_lines):
    if not msg_lines:
        return None
    first_line = msg_lines[0].strip()
    if first_line.startswith(">"):
        room_id = first_line[1:].strip()
        if room_id.startswith("battle-"):
            return room_id
    return None


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


def _collect_known_pokemon(msg_lines, known_pkmn_names):
    for line in msg_lines:
        split_line = line.split("|")
        if len(split_line) < 2:
            continue
        action = split_line[1].strip()
        if action in {"poke", "switch", "drag", "replace", "detailschange"}:
            if len(split_line) >= 4:
                pkmn_name = normalize_name(split_line[3].split(",")[0])
                if pkmn_name:
                    known_pkmn_names.add(pkmn_name)


def _collect_battle_factory_tier(msg_lines):
    for line in msg_lines:
        if "Battle Factory Tier:" in line:
            return extract_battle_factory_tier_from_msg(line)
    return None


def _resolve_player_sides(player_map):
    normalized_user = normalize_name(FoulPlayConfig.user_id or FoulPlayConfig.username)
    normalized_username = normalize_name(FoulPlayConfig.username)
    for side_id, account in player_map.items():
        normalized_account = normalize_name(account)
        if normalized_account in {normalized_user, normalized_username}:
            opponent_side = constants.ID_LOOKUP.get(side_id)
            return side_id, opponent_side, player_map.get(opponent_side)
    return None, None, None


async def async_pick_move(battle, return_policy: bool = False):
    start_time = time.time()
    battle_copy = deepcopy(battle)
    if not battle_copy.team_preview:
        battle_copy.user.update_from_request_json(battle_copy.request_json)

    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        if return_policy:
            best_move, policy = await loop.run_in_executor(
                pool, find_best_move_with_policy, battle_copy
            )
        else:
            best_move = await loop.run_in_executor(pool, find_best_move, battle_copy)
            policy = None
    elapsed_ms = int((time.time() - start_time) * 1000)
    battle.user.last_selected_move = LastUsedMove(
        battle.user.active.name,
        best_move.removesuffix("-tera").removesuffix("-mega"),
        battle.turn,
    )
    try:
        battle.search_times_ms.append(elapsed_ms)
        battle.decision_count += 1
        decision_entry = {
            "turn": battle.turn or 0,
            "decision": best_move,
            "search_time_ms": elapsed_ms,
            "tags": _move_reason_tags(battle, best_move),
        }
        if return_policy and policy:
            decision_entry["policy_top"] = [
                {
                    "move": move,
                    "weight": round(weight, 6),
                    "tags": _move_reason_tags(battle, move),
                }
                for move, weight in policy[:3]
            ]
        battle.decision_log.append(decision_entry)
    except Exception as exc:
        logger.debug("Telemetry capture failed: %s", exc)
    if return_policy:
        return format_decision(battle_copy, best_move), policy
    return format_decision(battle_copy, best_move)


async def handle_team_preview(battle, ps_websocket_client):
    battle_copy = deepcopy(battle)
    battle_copy.user.active = Pokemon.get_dummy()
    battle_copy.opponent.active = Pokemon.get_dummy()
    battle_copy.team_preview = True

    if FoulPlayConfig.suggest_only:
        best_move, policy = await async_pick_move(battle_copy, return_policy=True)
        log_suggested_moves(battle, policy)
    else:
        best_move = await async_pick_move(battle_copy)

    # because we copied the battle before sending it in, we need to update the last selected move here
    pkmn_name = battle.user.reserve[int(best_move[0].split()[1]) - 1].name
    battle.user.last_selected_move = LastUsedMove(
        "teampreview", "switch {}".format(pkmn_name), battle.turn
    )

    size_of_team = len(battle.user.reserve) + 1
    team_list_indexes = list(range(1, size_of_team))
    choice_digit = int(best_move[0].split()[-1])

    team_list_indexes.remove(choice_digit)
    message = [
        "/team {}{}|{}".format(
            choice_digit, "".join(str(x) for x in team_list_indexes), battle.rqid
        )
    ]

    if FoulPlayConfig.suggest_only:
        logger.info("Suggested team preview: %s", message[0])
        return

    await ps_websocket_client.send_message(battle.battle_tag, message)


async def get_battle_tag_and_opponent(ps_websocket_client: PSWebsocketClient):
    while True:
        msg = await ps_websocket_client.receive_message()
        split_msg = msg.split("|")
        first_msg = split_msg[0]
        if "battle" in first_msg:
            battle_tag = first_msg.replace(">", "").strip()
            user_name = FoulPlayConfig.username
            opponent_name = (
                split_msg[4].replace(user_name, "").replace("vs.", "").strip()
            )
            logger.info("Initialized {} against: {}".format(battle_tag, opponent_name))
            return battle_tag, opponent_name


async def start_battle_common(
    ps_websocket_client: PSWebsocketClient, pokemon_battle_type
):
    battle_tag, opponent_name = await get_battle_tag_and_opponent(ps_websocket_client)
    _write_last_battle_tag(battle_tag)
    if FoulPlayConfig.log_to_file:
        FoulPlayConfig.file_log_handler.do_rollover(
            "{}_{}.log".format(battle_tag, opponent_name)
        )

    battle = Battle(battle_tag)
    battle.started_at = time.time()
    battle.opponent.account_name = opponent_name
    battle.pokemon_format = pokemon_battle_type
    battle.generation = pokemon_battle_type[:4]

    # wait until the opponent's identifier is received. This will be `p1` or `p2`.
    #
    # e.g.
    # '>battle-gen9randombattle-44733
    # |player|p1|OpponentName|2|'
    while True:
        msg = await ps_websocket_client.receive_message()
        if "|player|" in msg and battle.opponent.account_name in msg:
            battle.opponent.name = msg.split("|")[2]
            battle.user.name = constants.ID_LOOKUP[battle.opponent.name]
            break

    return battle, msg


async def get_first_request_json(
    ps_websocket_client: PSWebsocketClient, battle: Battle
):
    while True:
        msg = await ps_websocket_client.receive_message()
        msg_split = msg.split("|")
        if msg_split[1].strip() == "request" and msg_split[2].strip():
            user_json = json.loads(msg_split[2].strip("'"))
            battle.request_json = user_json
            battle.user.initialize_first_turn_user_from_json(user_json)
            battle.rqid = user_json[constants.RQID]
            return


async def start_random_battle(
    ps_websocket_client: PSWebsocketClient, pokemon_battle_type
):
    battle, msg = await start_battle_common(ps_websocket_client, pokemon_battle_type)
    battle.battle_type = BattleType.RANDOM_BATTLE
    RandomBattleTeamDatasets.initialize(battle.generation)

    while True:
        if constants.START_STRING in msg:
            battle.started = True

            # hold onto some messages to apply after we get the request JSON
            # omit the bot's switch-in message because we won't need that
            # parsing the request JSON will set the bot's active pkmn
            battle.msg_list = [
                m
                for m in msg.split(constants.START_STRING)[1].strip().split("\n")
                if not (m.startswith("|switch|{}".format(battle.user.name)))
            ]
            break
        msg = await ps_websocket_client.receive_message()

    await get_first_request_json(ps_websocket_client, battle)

    # apply the messages that were held onto
    process_battle_updates(battle)

    if FoulPlayConfig.suggest_only:
        best_move, policy = await async_pick_move(battle, return_policy=True)
        log_suggested_moves(battle, policy)
        logger.info("Suggested move: %s", best_move[0])
    else:
        best_move = await async_pick_move(battle)
        await ps_websocket_client.send_message(battle.battle_tag, best_move)

    return battle


async def start_standard_battle(
    ps_websocket_client: PSWebsocketClient, pokemon_battle_type, team_dict
):
    battle, msg = await start_battle_common(ps_websocket_client, pokemon_battle_type)
    battle.user.team_dict = team_dict
    if "battlefactory" in pokemon_battle_type:
        battle.battle_type = BattleType.BATTLE_FACTORY
    else:
        battle.battle_type = BattleType.STANDARD_BATTLE

    if battle.generation in constants.NO_TEAM_PREVIEW_GENS:
        while True:
            if constants.START_STRING in msg:
                battle.started = True

                # hold onto some messages to apply after we get the request JSON
                # omit the bot's switch-in message because we won't need that
                # parsing the request JSON will set the bot's active pkmn
                battle.msg_list = [
                    m
                    for m in msg.split(constants.START_STRING)[1].strip().split("\n")
                    if not (m.startswith("|switch|{}".format(battle.user.name)))
                ]
                break
            msg = await ps_websocket_client.receive_message()

        await get_first_request_json(ps_websocket_client, battle)

        unique_pkmn_names = set(
            [p.name for p in battle.user.reserve] + [battle.user.active.name]
        )
        SmogonSets.initialize(
            FoulPlayConfig.smogon_stats or pokemon_battle_type, unique_pkmn_names
        )
        TeamDatasets.initialize(pokemon_battle_type, unique_pkmn_names)

        # apply the messages that were held onto
        process_battle_updates(battle)

        if FoulPlayConfig.suggest_only:
            best_move, policy = await async_pick_move(battle, return_policy=True)
            log_suggested_moves(battle, policy)
            logger.info("Suggested move: %s", best_move[0])
        else:
            best_move = await async_pick_move(battle)
            await ps_websocket_client.send_message(battle.battle_tag, best_move)

    else:
        while constants.START_TEAM_PREVIEW not in msg:
            msg = await ps_websocket_client.receive_message()

        preview_string_lines = msg.split(constants.START_TEAM_PREVIEW)[-1].split("\n")

        opponent_pokemon = []
        for line in preview_string_lines:
            if not line:
                continue

            split_line = line.split("|")
            if (
                split_line[1] == constants.TEAM_PREVIEW_POKE
                and split_line[2].strip() == battle.opponent.name
            ):
                opponent_pokemon.append(split_line[3])

        await get_first_request_json(ps_websocket_client, battle)
        battle.initialize_team_preview(opponent_pokemon, pokemon_battle_type)
        battle.during_team_preview()

        unique_pkmn_names = set(
            p.name for p in battle.opponent.reserve + battle.user.reserve
        )

        if battle.battle_type == BattleType.BATTLE_FACTORY:
            battle.battle_type = BattleType.BATTLE_FACTORY
            tier_name = extract_battle_factory_tier_from_msg(msg)
            logger.info("Battle Factory Tier: {}".format(tier_name))
            TeamDatasets.initialize(
                pokemon_battle_type,
                unique_pkmn_names,
                battle_factory_tier_name=tier_name,
            )
        else:
            battle.battle_type = BattleType.STANDARD_BATTLE
            SmogonSets.initialize(
                FoulPlayConfig.smogon_stats or pokemon_battle_type, unique_pkmn_names
            )
            TeamDatasets.initialize(pokemon_battle_type, unique_pkmn_names)

        await handle_team_preview(battle, ps_websocket_client)

    return battle


async def start_battle(ps_websocket_client, pokemon_battle_type, team_dict):
    if "random" in pokemon_battle_type:
        battle = await start_random_battle(ps_websocket_client, pokemon_battle_type)
    else:
        battle = await start_standard_battle(
            ps_websocket_client, pokemon_battle_type, team_dict
        )

    # await ps_websocket_client.send_message(battle.battle_tag, ["hf"])
    if FoulPlayConfig.battle_timer != "none":
        await ps_websocket_client.send_message(
            battle.battle_tag, ["/timer {}".format(FoulPlayConfig.battle_timer)]
        )

    return battle


async def run_battle_loop(ps_websocket_client, battle):
    ps_websocket_client.consume_reconnect_flag()
    while True:
        msg = await ps_websocket_client.receive_message()
        if battle.win_reason is None:
            battle.win_reason = _extract_win_reason(msg)
        _update_opponent_tendencies(battle, msg)
        if ps_websocket_client.consume_reconnect_flag():
            if _message_indicates_battle_end(battle.battle_tag, msg):
                winner = _extract_winner_from_msg(msg)
                logger.info("Winner: {}".format(winner))
                await ps_websocket_client.send_message(battle.battle_tag, ["gg"])
                if (
                    FoulPlayConfig.save_replay == SaveReplay.always
                    or (
                        FoulPlayConfig.save_replay == SaveReplay.on_loss
                        and winner != FoulPlayConfig.username
                    )
                    or (
                        FoulPlayConfig.save_replay == SaveReplay.on_win
                        and winner == FoulPlayConfig.username
                    )
                ):
                    await ps_websocket_client.save_replay(battle.battle_tag)
                    battle.replay_saved = True
                    battle.replay_url = "https://replay.pokemonshowdown.com/{}".format(
                        battle.battle_tag
                    )
                if winner is None:
                    battle.win_reason = battle.win_reason or "tie"
                else:
                    battle.win_reason = battle.win_reason or "normal"
                _write_battle_summary(
                    battle, winner, ps_websocket_client.reconnect_count
                )
                _clear_last_battle_tag()
                await ps_websocket_client.leave_battle(battle.battle_tag)
                return winner
            logger.warning(
                "Websocket reconnected during battle %s; resuming battle state",
                battle.battle_tag,
            )
            return RECONNECT_RESUME
        if battle_is_finished(battle.battle_tag, msg):
            winner = _extract_winner_from_msg(msg)
            logger.info("Winner: {}".format(winner))
            await ps_websocket_client.send_message(battle.battle_tag, ["gg"])
            if (
                FoulPlayConfig.save_replay == SaveReplay.always
                or (
                    FoulPlayConfig.save_replay == SaveReplay.on_loss
                    and winner != FoulPlayConfig.username
                )
                or (
                    FoulPlayConfig.save_replay == SaveReplay.on_win
                    and winner == FoulPlayConfig.username
                )
            ):
                await ps_websocket_client.save_replay(battle.battle_tag)
                battle.replay_saved = True
                battle.replay_url = "https://replay.pokemonshowdown.com/{}".format(
                    battle.battle_tag
                )
            if winner is None:
                battle.win_reason = battle.win_reason or "tie"
            else:
                battle.win_reason = battle.win_reason or "normal"
            _write_battle_summary(
                battle, winner, ps_websocket_client.reconnect_count
            )
            _clear_last_battle_tag()
            await ps_websocket_client.leave_battle(battle.battle_tag)
            return winner
        else:
            action_required = await async_update_battle(battle, msg)
            if action_required and not battle.wait:
                if FoulPlayConfig.suggest_only:
                    best_move, policy = await async_pick_move(
                        battle, return_policy=True
                    )
                    log_suggested_moves(battle, policy)
                    logger.info("Suggested move: %s", best_move[0])
                else:
                    best_move = await async_pick_move(battle)
                    await ps_websocket_client.send_message(
                        battle.battle_tag, best_move
                    )


async def _resume_battle_state(ps_websocket_client, pokemon_battle_type, battle_tag):
    if battle_tag is None:
        raise ValueError("battle_tag is required to resume a battle")

    _write_last_battle_tag(battle_tag)
    await ps_websocket_client.join_room(battle_tag)

    backlog_msgs = []
    player_map = {}
    known_pkmn_names = set()
    battle_factory_tier = None
    request_json = None

    while True:
        msg = await ps_websocket_client.receive_message()
        msg_lines = msg.split("\n")
        room_id = _extract_battle_room_id(msg_lines)
        if room_id is None:
            continue
        if room_id != battle_tag:
            continue
        if _message_indicates_battle_end(battle_tag, msg):
            winner = _extract_winner_from_msg(msg)
            if constants.TIE_STRING in msg:
                win_reason = "tie"
            elif winner is None:
                win_reason = _extract_win_reason(msg) or "deinit"
            else:
                win_reason = _extract_win_reason(msg) or "normal"
            logger.info(
                "Battle %s ended during reconnect. Winner: %s",
                battle_tag,
                winner,
            )
            _clear_last_battle_tag()
            return None, {"winner": winner, "win_reason": win_reason}

        backlog_msgs.append(msg)
        _collect_player_map(msg_lines, player_map)
        _collect_known_pokemon(msg_lines, known_pkmn_names)
        if battle_factory_tier is None:
            battle_factory_tier = _collect_battle_factory_tier(msg_lines)

        request_json = request_json or _extract_request_json(msg_lines)
        if request_json is not None and len(player_map) >= 2:
            break

    if request_json is None:
        raise ValueError("Did not receive request JSON while resuming battle")

    for pkmn_dict in request_json[constants.SIDE][constants.POKEMON]:
        pkmn_name = normalize_name(pkmn_dict[constants.DETAILS].split(",")[0])
        if pkmn_name:
            known_pkmn_names.add(pkmn_name)

    battle = Battle(battle_tag)
    battle.started_at = time.time()
    battle.pokemon_format = pokemon_battle_type
    battle.generation = pokemon_battle_type[:4]

    if "random" in pokemon_battle_type:
        battle.battle_type = BattleType.RANDOM_BATTLE
    elif "battlefactory" in pokemon_battle_type:
        battle.battle_type = BattleType.BATTLE_FACTORY
    else:
        battle.battle_type = BattleType.STANDARD_BATTLE

    user_side, opponent_side, opponent_account_name = _resolve_player_sides(player_map)
    if user_side is None or opponent_side is None:
        raise ValueError(
            "Could not match logged-in user to battle players: {}".format(player_map)
        )

    battle.user.name = user_side
    battle.opponent.name = opponent_side
    battle.opponent.account_name = opponent_account_name

    if FoulPlayConfig.log_to_file:
        FoulPlayConfig.file_log_handler.do_rollover(
            "{}_{}.log".format(battle_tag, opponent_account_name or "unknown")
        )

    if battle.battle_type == BattleType.RANDOM_BATTLE:
        RandomBattleTeamDatasets.initialize(battle.generation)
    elif known_pkmn_names:
        if battle.battle_type == BattleType.BATTLE_FACTORY:
            if battle_factory_tier is None:
                logger.warning(
                    "Battle Factory tier not found; using default team datasets"
                )
                TeamDatasets.initialize(pokemon_battle_type, known_pkmn_names)
            else:
                TeamDatasets.initialize(
                    pokemon_battle_type,
                    known_pkmn_names,
                    battle_factory_tier_name=battle_factory_tier,
                )
        else:
            SmogonSets.initialize(
                FoulPlayConfig.smogon_stats or pokemon_battle_type, known_pkmn_names
            )
            TeamDatasets.initialize(pokemon_battle_type, known_pkmn_names)

    action_required = False
    for msg in backlog_msgs:
        action_required = update_battle(battle, msg)
        if battle.request_json is not None:
            break

    if battle.request_json is None:
        raise ValueError("Resume battle did not receive request JSON")

    try:
        battle.user.update_from_request_json(battle.request_json)
    except Exception as exc:
        logger.warning("Failed to update user data from request JSON: %s", exc)
        battle.user.initialize_first_turn_user_from_json(battle.request_json)

    battle.started = True
    if battle.rqid is None:
        battle.rqid = battle.request_json.get(constants.RQID)

    if FoulPlayConfig.battle_timer != "none":
        await ps_websocket_client.send_message(
            battle.battle_tag, ["/timer {}".format(FoulPlayConfig.battle_timer)]
        )

    if action_required and not battle.wait:
        if FoulPlayConfig.suggest_only:
            best_move, policy = await async_pick_move(battle, return_policy=True)
            log_suggested_moves(battle, policy)
            logger.info("Suggested move: %s", best_move[0])
        else:
            best_move = await async_pick_move(battle)
            await ps_websocket_client.send_message(battle.battle_tag, best_move)

    return battle, None


async def _run_battle_loop_with_auto_resume(
    ps_websocket_client, battle, pokemon_battle_type
):
    while True:
        result = await run_battle_loop(ps_websocket_client, battle)
        if result is RECONNECT_RESUME:
            resumed_battle, finished = await _resume_battle_state(
                ps_websocket_client, pokemon_battle_type, battle.battle_tag
            )
            if finished is not None:
                battle.win_reason = battle.win_reason or finished.get("win_reason")
                _write_battle_summary(
                    battle, finished.get("winner"), ps_websocket_client.reconnect_count
                )
                return finished.get("winner")
            battle = resumed_battle
            continue
        return result


async def resume_battle(ps_websocket_client, pokemon_battle_type, battle_tag):
    battle, finished = await _resume_battle_state(
        ps_websocket_client, pokemon_battle_type, battle_tag
    )
    if finished is not None:
        return finished.get("winner")
    return await _run_battle_loop_with_auto_resume(
        ps_websocket_client, battle, pokemon_battle_type
    )


def _write_battle_summary(battle, winner, reconnect_count=0):
    if not FoulPlayConfig.summary_path and not FoulPlayConfig.summary_json_path:
        return

    turns = battle.turn or 0
    search_times = list(battle.search_times_ms or [])
    decision_count = battle.decision_count or len(search_times)
    search_time_summary = {}
    if search_times:
        total_ms = int(sum(search_times))
        search_time_summary = {
            "total": total_ms,
            "avg": round(total_ms / max(decision_count, 1), 2),
            "max": int(max(search_times)),
        }
    summary = {
        "battle_tag": battle.battle_tag,
        "format": battle.pokemon_format,
        "winner": winner,
        "win_reason": battle.win_reason,
        "turns": turns,
        "bot_mode": FoulPlayConfig.bot_mode.name,
        "risk_mode": FoulPlayConfig.risk_mode.name,
        "suggest_only": FoulPlayConfig.suggest_only,
        "decision_count": decision_count,
        "search_time_ms": search_time_summary,
        "reconnect_count": reconnect_count,
        "replay_saved": battle.replay_saved,
        "replay_url": battle.replay_url,
        "opponent_tendencies": battle.opponent_tendencies,
        "timestamp_utc": datetime.utcnow().isoformat() + "Z",
    }
    if battle.decision_log:
        summary["decision_log"] = battle.decision_log
    if battle.started_at:
        summary["duration_seconds"] = int(time.time() - battle.started_at)

    if FoulPlayConfig.summary_path:
        lines = [
            "battle_tag: {}".format(summary["battle_tag"]),
            "format: {}".format(summary["format"]),
            "winner: {}".format(summary["winner"]),
            "win_reason: {}".format(summary["win_reason"]),
            "turns: {}".format(summary["turns"]),
            "bot_mode: {}".format(summary["bot_mode"]),
            "risk_mode: {}".format(summary["risk_mode"]),
            "suggest_only: {}".format(summary["suggest_only"]),
            "decision_count: {}".format(summary["decision_count"]),
            "search_time_ms: {}".format(summary["search_time_ms"]),
            "reconnect_count: {}".format(summary["reconnect_count"]),
            "replay_saved: {}".format(summary["replay_saved"]),
            "replay_url: {}".format(summary["replay_url"]),
            "timestamp_utc: {}".format(summary["timestamp_utc"]),
        ]
        if "duration_seconds" in summary:
            lines.append(
                "duration_seconds: {}".format(summary["duration_seconds"])
            )
        with open(FoulPlayConfig.summary_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n\n")

    if FoulPlayConfig.summary_json_path:
        with open(FoulPlayConfig.summary_json_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(summary) + "\n")


async def pokemon_battle(ps_websocket_client, pokemon_battle_type, team_dict):
    battle = await start_battle(ps_websocket_client, pokemon_battle_type, team_dict)
    return await _run_battle_loop_with_auto_resume(
        ps_websocket_client, battle, pokemon_battle_type
    )
