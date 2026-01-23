import atexit
import logging
import random
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy

from constants import BattleType
import constants
from data import all_move_json
from fp.battle import Battle
from config import FoulPlayConfig, RiskModes
from .standard_battles import prepare_battles
from .random_battles import prepare_random_battles

from poke_engine import State as PokeEngineState, monte_carlo_tree_search, MctsResult

from fp.search.poke_engine_helpers import battle_to_poke_engine_state
from fp.helpers import normalize_name

logger = logging.getLogger(__name__)

_executor = None
_executor_workers = None


def _shutdown_executor():
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None


atexit.register(_shutdown_executor)


def _get_process_pool() -> ProcessPoolExecutor:
    global _executor, _executor_workers
    if _executor is None or _executor_workers != FoulPlayConfig.parallelism:
        if _executor is not None:
            _executor.shutdown(wait=False, cancel_futures=True)
        _executor = ProcessPoolExecutor(max_workers=FoulPlayConfig.parallelism)
        _executor_workers = FoulPlayConfig.parallelism
    return _executor


def compute_final_policy(
    mcts_results: list[(MctsResult, float, int)]
) -> list[tuple[str, float]]:
    final_policy = {}
    for mcts_result, sample_chance, index in mcts_results:
        this_policy = max(mcts_result.side_one, key=lambda x: x.visits)
        logger.info(
            "Policy {}: {} visited {}% avg_score={} sample_chance_multiplier={}".format(
                index,
                this_policy.move_choice,
                round(100 * this_policy.visits / mcts_result.total_visits, 2),
                round(this_policy.total_score / this_policy.visits, 3),
                round(sample_chance, 3),
            )
        )
        for s1_option in mcts_result.side_one:
            final_policy[s1_option.move_choice] = final_policy.get(
                s1_option.move_choice, 0
            ) + (sample_chance * (s1_option.visits / mcts_result.total_visits))

    return sorted(final_policy.items(), key=lambda x: x[1], reverse=True)


def _is_setup_move(move_json):
    if constants.BOOSTS in move_json:
        return True
    if (
        constants.SELF in move_json
        and constants.BOOSTS in move_json[constants.SELF]
    ):
        return True
    return False


_PROTECT_MOVE_IDS = set(
    constants.PROTECT_VOLATILE_STATUSES
    + ["detect", "kingsshield", "obstruct", "silktrap"]
)


def _decision_tags(decision: str) -> set[str]:
    decision = decision.removesuffix("-tera").removesuffix("-mega")
    tags = set()
    if decision.startswith(constants.SWITCH_STRING + " "):
        tags.add("switch")
        return tags

    move_id = normalize_name(decision)
    if move_id in constants.SWITCH_OUT_MOVES:
        tags.add("pivot")
    if move_id in _PROTECT_MOVE_IDS:
        tags.add("protect")

    move_json = all_move_json.get(move_id)
    if move_json is None:
        return tags

    if move_json.get(constants.CATEGORY) == constants.STATUS:
        tags.add("status")
    else:
        tags.add("attack")

    if _is_setup_move(move_json):
        tags.add("setup")

    return tags


def _apply_opponent_tendency_bias(
    battle: Battle, final_policy: list[tuple[str, float]]
) -> list[tuple[str, float]]:
    tendencies = getattr(battle, "opponent_tendencies", None)
    if not tendencies:
        return final_policy

    actions = tendencies.get("actions", 0)
    moves = tendencies.get("moves", 0)
    if actions < 5:
        return final_policy

    switch_rate = tendencies.get("switches", 0) / max(actions, 1)
    protect_rate = tendencies.get("protects", 0) / max(moves, 1)

    if switch_rate < 0.35 and protect_rate < 0.25:
        return final_policy

    adjusted = []
    for move, weight in final_policy:
        tags = _decision_tags(move)
        multiplier = 1.0
        if switch_rate >= 0.45:
            if "pivot" in tags or "setup" in tags or "status" in tags:
                multiplier += 0.08
        if protect_rate >= 0.3:
            if "setup" in tags or "status" in tags or "switch" in tags:
                multiplier += 0.05
        adjusted.append((move, weight * multiplier))

    adjusted.sort(key=lambda x: x[1], reverse=True)
    logger.info(
        "Opponent tendency bias: switch_rate=%.2f protect_rate=%.2f",
        switch_rate,
        protect_rate,
    )
    return adjusted


def _get_risk_mode_threshold(risk_mode: RiskModes) -> float:
    if risk_mode == RiskModes.safe:
        return 0.9
    if risk_mode == RiskModes.aggressive:
        return 0.6
    return 0.75


def _get_risk_mode_weight_power(risk_mode: RiskModes) -> float:
    if risk_mode == RiskModes.aggressive:
        return 0.7
    return 1.0


def _resolve_risk_mode(battle: Battle | None) -> RiskModes:
    if FoulPlayConfig.risk_mode != RiskModes.auto:
        return FoulPlayConfig.risk_mode
    if battle is None or battle.team_preview:
        return RiskModes.balanced

    user_alive = _count_alive(battle.user)
    opp_alive = _count_alive(battle.opponent)
    if user_alive <= 2 and user_alive < opp_alive:
        return RiskModes.aggressive
    if opp_alive <= 2 and user_alive > opp_alive:
        return RiskModes.safe

    user_hp_pct = _get_hp_pct(battle.user.active)
    opp_hp_pct = _get_hp_pct(battle.opponent.active)
    if user_hp_pct is not None and opp_hp_pct is not None:
        if user_hp_pct + 0.2 < opp_hp_pct:
            return RiskModes.aggressive
        if user_hp_pct > opp_hp_pct + 0.2:
            return RiskModes.safe

    return RiskModes.balanced


def select_move_from_policy(
    final_policy: list[tuple[str, float]],
    risk_mode: RiskModes,
    configured_risk_mode: RiskModes | None = None,
) -> str:
    if not final_policy:
        raise ValueError("No moves available from MCTS results")

    configured_risk_mode = configured_risk_mode or risk_mode

    # Consider all moves that are close to the best move
    highest_percentage = final_policy[0][1]
    threshold = _get_risk_mode_threshold(risk_mode)
    considered_choices = [
        i for i in final_policy if i[1] >= highest_percentage * threshold
    ]
    if not considered_choices:
        considered_choices = final_policy[:1]
    if configured_risk_mode == RiskModes.auto:
        logger.info(
            "Risk mode: auto -> {}".format(risk_mode.name)
        )
    else:
        logger.info("Risk mode: {}".format(risk_mode.name))
    logger.info("Considered Choices:")
    for i, policy in enumerate(considered_choices):
        logger.info("\t{}%: {}".format(round(policy[1] * 100, 3), policy[0]))

    if risk_mode == RiskModes.safe:
        return max(considered_choices, key=lambda x: x[1])[0]

    weight_power = _get_risk_mode_weight_power(risk_mode)
    weights = [p[1] ** weight_power for p in considered_choices]
    choice = random.choices(considered_choices, weights=weights)[0]
    return choice[0]


def select_move_from_mcts_results(mcts_results: list[(MctsResult, float, int)]) -> str:
    final_policy = compute_final_policy(mcts_results)
    resolved_risk_mode = _resolve_risk_mode(None)
    return select_move_from_policy(
        final_policy, resolved_risk_mode, FoulPlayConfig.risk_mode
    )


def get_result_from_mcts(state: str, search_time_ms: int, index: int) -> MctsResult:
    logger.debug("Calling with {} state: {}".format(index, state))
    poke_engine_state = PokeEngineState.from_string(state)

    res = monte_carlo_tree_search(poke_engine_state, search_time_ms)
    logger.info("Iterations {}: {}".format(index, res.total_visits))
    return res


def search_time_num_battles_randombattles(battle):
    revealed_pkmn = len(battle.opponent.reserve)
    if battle.opponent.active is not None:
        revealed_pkmn += 1

    opponent_active_num_moves = len(battle.opponent.active.moves)
    in_time_pressure = battle.time_remaining is not None and battle.time_remaining <= 60

    # it is still quite early in the battle and the pkmn in front of us
    # hasn't revealed any moves: search a lot of battles shallowly
    if (
        revealed_pkmn <= 3
        and battle.opponent.active.hp > 0
        and opponent_active_num_moves == 0
    ):
        num_battles_multiplier = 2 if in_time_pressure else 4
        num_battles = FoulPlayConfig.parallelism * num_battles_multiplier
        num_battles = _adjust_num_battles_for_branching(battle, num_battles)
        search_time_ms = int(
            FoulPlayConfig.search_time_ms // 2
        )
        return num_battles, _apply_dynamic_search_time(battle, search_time_ms)

    else:
        num_battles_multiplier = 1 if in_time_pressure else 2
        num_battles = FoulPlayConfig.parallelism * num_battles_multiplier
        num_battles = _adjust_num_battles_for_branching(battle, num_battles)
        search_time_ms = int(
            FoulPlayConfig.search_time_ms
        )
        return num_battles, _apply_dynamic_search_time(battle, search_time_ms)


def search_time_num_battles_standard_battle(battle):
    opponent_active_num_moves = len(battle.opponent.active.moves)
    in_time_pressure = battle.time_remaining is not None and battle.time_remaining <= 60

    if (
        battle.team_preview
        or (battle.opponent.active.hp > 0 and opponent_active_num_moves == 0)
        or opponent_active_num_moves < 3
    ):
        num_battles_multiplier = 1 if in_time_pressure else 2
        num_battles = FoulPlayConfig.parallelism * num_battles_multiplier
        num_battles = _adjust_num_battles_for_branching(battle, num_battles)
        search_time_ms = int(
            FoulPlayConfig.search_time_ms
        )
        return num_battles, _apply_dynamic_search_time(battle, search_time_ms)
    else:
        num_battles = _adjust_num_battles_for_branching(
            battle, FoulPlayConfig.parallelism
        )
        return num_battles, _apply_dynamic_search_time(
            battle, FoulPlayConfig.search_time_ms
        )


def _apply_dynamic_search_time(battle: Battle, search_time_ms: int) -> int:
    if battle.team_preview:
        return search_time_ms

    multiplier = 1.0
    turn = battle.turn or 0
    if turn >= 20:
        multiplier += 0.25
    if turn >= 30:
        multiplier += 0.25

    user_hp_pct = _get_hp_pct(battle.user.active)
    opp_hp_pct = _get_hp_pct(battle.opponent.active)
    if user_hp_pct is not None and user_hp_pct <= 0.25:
        multiplier += 0.25
    if opp_hp_pct is not None and opp_hp_pct <= 0.25:
        multiplier += 0.25

    if _count_alive(battle.user) <= 2 or _count_alive(battle.opponent) <= 2:
        multiplier += 0.25

    if battle.time_remaining is not None:
        if battle.time_remaining <= 30:
            multiplier *= 0.5
        elif battle.time_remaining <= 60:
            multiplier *= 0.75

    branching_factor = _estimate_branching_factor(battle)
    if branching_factor <= 2:
        multiplier *= 0.7
    elif branching_factor <= 3:
        multiplier *= 0.85
    elif branching_factor >= 8:
        multiplier *= 1.25
    elif branching_factor >= 6:
        multiplier *= 1.15

    multiplier = min(max(multiplier, 0.5), 2.0)
    return max(25, int(search_time_ms * multiplier))


def _estimate_branching_factor(battle: Battle) -> int:
    if battle.team_preview:
        return max(1, len(battle.user.reserve) + (1 if battle.user.active else 0))

    if battle.user.active is None:
        return 1

    if battle.force_switch:
        num_moves = 0
    else:
        num_moves = sum(
            1
            for m in battle.user.active.moves
            if not m.disabled and m.current_pp > 0
        )
        if num_moves == 0:
            num_moves = 1

    num_switches = 0
    if battle.force_switch or not battle.user.trapped:
        for pkmn in battle.user.reserve:
            if pkmn.is_alive():
                num_switches += 1

    return max(1, num_moves + num_switches)


def _adjust_num_battles_for_branching(battle: Battle, num_battles: int) -> int:
    if battle.team_preview:
        return num_battles
    branching_factor = _estimate_branching_factor(battle)
    if branching_factor <= 2:
        return max(1, int(num_battles * 0.7))
    if branching_factor <= 3:
        return max(1, int(num_battles * 0.85))
    if branching_factor >= 8:
        return max(1, int(num_battles * 1.2))
    if branching_factor >= 6:
        return max(1, int(num_battles * 1.1))
    return num_battles


def _policy_confidence_ratio(final_policy: list[tuple[str, float]]) -> float:
    if len(final_policy) < 2:
        return float("inf")
    top = final_policy[0][1]
    second = final_policy[1][1]
    if second <= 0:
        return float("inf")
    return top / second


def _run_mcts_batch(battles, search_time_ms: int):
    executor = _get_process_pool()
    futures = []
    for index, (b, chance) in enumerate(battles):
        fut = executor.submit(
            get_result_from_mcts,
            battle_to_poke_engine_state(b).to_string(),
            search_time_ms,
            index,
        )
        futures.append((fut, chance, index))
    return [(fut.result(), chance, index) for (fut, chance, index) in futures]


def _get_hp_pct(pokemon):
    if pokemon is None or pokemon.max_hp == 0:
        return None
    return pokemon.hp / pokemon.max_hp


def _count_alive(battler):
    alive = 0
    if battler.active is not None and battler.active.is_alive():
        alive += 1
    for pkmn in battler.reserve:
        if pkmn.is_alive():
            alive += 1
    return alive


def find_best_move_with_policy(battle: Battle) -> tuple[str, list[tuple[str, float]]]:
    battle = deepcopy(battle)
    if battle.team_preview:
        battle.user.active = battle.user.reserve.pop(0)
        battle.opponent.active = battle.opponent.reserve.pop(0)

    if battle.battle_type == BattleType.RANDOM_BATTLE:
        num_battles, search_time_per_battle = search_time_num_battles_randombattles(
            battle
        )
        battles = prepare_random_battles(battle, num_battles)
    elif battle.battle_type == BattleType.BATTLE_FACTORY:
        num_battles, search_time_per_battle = search_time_num_battles_standard_battle(
            battle
        )
        battles = prepare_random_battles(battle, num_battles)
    elif battle.battle_type == BattleType.STANDARD_BATTLE:
        num_battles, search_time_per_battle = search_time_num_battles_standard_battle(
            battle
        )
        battles = prepare_battles(battle, num_battles)
    else:
        raise ValueError("Unsupported battle type: {}".format(battle.battle_type))

    logger.info("Searching for a move using MCTS...")
    logger.info(
        "Sampling {} battles at {}ms each".format(num_battles, search_time_per_battle)
    )
    mcts_results = _run_mcts_batch(battles, search_time_per_battle)
    final_policy = compute_final_policy(mcts_results)
    final_policy = _apply_opponent_tendency_bias(battle, final_policy)
    confidence_ratio = _policy_confidence_ratio(final_policy)

    in_time_pressure = battle.time_remaining is not None and battle.time_remaining <= 60
    if not in_time_pressure and confidence_ratio < 1.12:
        max_time = int(FoulPlayConfig.search_time_ms * 2)
        rerun_time = min(int(search_time_per_battle * 1.5), max_time)
        if rerun_time > search_time_per_battle:
            logger.info(
                "Low policy confidence (%.2f). Rerunning MCTS at %sms.",
                confidence_ratio,
                rerun_time,
            )
            mcts_results = _run_mcts_batch(battles, rerun_time)
            final_policy = compute_final_policy(mcts_results)
            final_policy = _apply_opponent_tendency_bias(battle, final_policy)
    resolved_risk_mode = _resolve_risk_mode(battle)
    choice = select_move_from_policy(
        final_policy, resolved_risk_mode, FoulPlayConfig.risk_mode
    )
    logger.info("Choice: {}".format(choice))
    return choice, final_policy


def find_best_move(battle: Battle) -> str:
    choice, _ = find_best_move_with_policy(battle)
    return choice
