import atexit
import logging
import random
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from copy import deepcopy

from fp import constants
from fp.battle.helpers import normalize_name
from fp.battle.state import Battle
from fp.config import FoulPlayConfig, RiskModes
from fp.data import all_move_json
from fp.search.poke_engine_helpers import battle_to_poke_engine_state

from poke_engine import State as PokeEngineState, monte_carlo_tree_search, MctsResult

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


def _reset_process_pool():
    global _executor, _executor_workers
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
    _executor = ProcessPoolExecutor(max_workers=FoulPlayConfig.parallelism)
    _executor_workers = FoulPlayConfig.parallelism


def compute_final_policy(
    mcts_results: list[tuple[MctsResult, float, int]],
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
        for option in mcts_result.side_one:
            final_policy[option.move_choice] = final_policy.get(
                option.move_choice, 0
            ) + (sample_chance * (option.visits / mcts_result.total_visits))
    return sorted(final_policy.items(), key=lambda x: x[1], reverse=True)


def _is_setup_move(move_json):
    return constants.BOOSTS in move_json or (
        constants.SELF in move_json and constants.BOOSTS in move_json[constants.SELF]
    )


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
    if move_json.get(constants.CATEGORY) == constants.MoveCategory.STATUS:
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
    if not tendencies or tendencies.get("actions", 0) < 5:
        return final_policy

    actions = tendencies.get("actions", 0)
    moves = tendencies.get("moves", 0)
    switch_rate = tendencies.get("switches", 0) / max(actions, 1)
    protect_rate = tendencies.get("protects", 0) / max(moves, 1)
    if switch_rate < 0.35 and protect_rate < 0.25:
        return final_policy

    adjusted = []
    for move, weight in final_policy:
        tags = _decision_tags(move)
        multiplier = 1.0
        if switch_rate >= 0.45 and tags.intersection({"pivot", "setup", "status"}):
            multiplier += 0.08
        if protect_rate >= 0.30 and tags.intersection({"setup", "status", "switch"}):
            multiplier += 0.05
        adjusted.append((move, weight * multiplier))
    adjusted.sort(key=lambda x: x[1], reverse=True)
    logger.info(
        "Opponent tendency bias: switch_rate=%.2f protect_rate=%.2f",
        switch_rate,
        protect_rate,
    )
    return adjusted


def _risk_threshold(risk_mode: RiskModes) -> float:
    if risk_mode == RiskModes.safe:
        return 0.90
    if risk_mode == RiskModes.aggressive:
        return 0.60
    return 0.75


def _configured_risk_mode() -> RiskModes:
    return getattr(FoulPlayConfig, "risk_mode", RiskModes.balanced)


def _resolve_risk_mode(battle: Battle | None) -> RiskModes:
    configured_risk_mode = _configured_risk_mode()
    if configured_risk_mode != RiskModes.auto:
        return configured_risk_mode
    if battle is None or battle.team_preview:
        return RiskModes.balanced

    user_alive = _count_alive(battle.user)
    opponent_alive = _count_alive(battle.opponent)
    if user_alive <= 2 and user_alive < opponent_alive:
        return RiskModes.aggressive
    if opponent_alive <= 2 and user_alive > opponent_alive:
        return RiskModes.safe

    user_hp = _get_hp_pct(battle.user.active)
    opponent_hp = _get_hp_pct(battle.opponent.active)
    if user_hp is not None and opponent_hp is not None:
        if user_hp + 0.20 < opponent_hp:
            return RiskModes.aggressive
        if user_hp > opponent_hp + 0.20:
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
    highest = final_policy[0][1]
    choices = [p for p in final_policy if p[1] >= highest * _risk_threshold(risk_mode)]
    choices = choices or final_policy[:1]

    if configured_risk_mode == RiskModes.auto:
        logger.info("Risk mode: auto -> %s", risk_mode.name)
    else:
        logger.info("Risk mode: %s", risk_mode.name)
    logger.info("Considered Choices:")
    for move, weight in choices:
        logger.info("\t{}%: {}".format(round(weight * 100, 3), move))

    if risk_mode == RiskModes.safe:
        return choices[0][0]
    weight_power = 0.7 if risk_mode == RiskModes.aggressive else 1.0
    return random.choices(choices, weights=[p[1] ** weight_power for p in choices])[0][
        0
    ]


def select_move_from_mcts_results(
    mcts_results: list[tuple[MctsResult, float, int]],
) -> str:
    policy = compute_final_policy(mcts_results)
    mode = _resolve_risk_mode(None)
    return select_move_from_policy(policy, mode, FoulPlayConfig.risk_mode)


def get_result_from_mcts(
    state: str, search_time_ms: int, index: int, threads: int
) -> MctsResult:
    logger.debug("Calling with {} state: {}".format(index, state))
    poke_engine_state = PokeEngineState.from_string(state)
    res = monte_carlo_tree_search(poke_engine_state, search_time_ms, threads=threads)
    logger.info("Iterations {}: {}".format(index, res.total_visits))
    return res


def _run_mcts_batch(battles, search_time_ms: int):
    for attempt in range(2):
        executor = _get_process_pool()
        futures = []
        try:
            for index, (battle, chance) in enumerate(battles):
                future = executor.submit(
                    get_result_from_mcts,
                    battle_to_poke_engine_state(battle).to_string(),
                    search_time_ms,
                    index,
                    FoulPlayConfig.search_threads,
                )
                futures.append((future, chance, index))
            return [(f.result(), chance, index) for f, chance, index in futures]
        except BrokenProcessPool:
            logger.warning("MCTS worker pool crashed; recreating pool")
            _reset_process_pool()
            if attempt == 1:
                raise


def _get_hp_pct(pokemon):
    if pokemon is None or pokemon.max_hp == 0:
        return None
    return pokemon.hp / pokemon.max_hp


def _count_alive(battler):
    alive = int(battler.active is not None and battler.active.is_alive())
    return alive + sum(1 for pokemon in battler.reserve if pokemon.is_alive())


def _estimate_branching_factor(battle: Battle) -> int:
    if battle.team_preview:
        return max(1, len(battle.user.reserve) + int(battle.user.active is not None))
    if battle.user.active is None:
        return 1

    num_moves = 0
    if not battle.force_switch:
        num_moves = (
            sum(
                1
                for move in battle.user.active.moves
                if not move.disabled and move.current_pp > 0
            )
            or 1
        )
    num_switches = 0
    if battle.force_switch or not battle.user.trapped:
        num_switches = sum(1 for pokemon in battle.user.reserve if pokemon.is_alive())
    return max(1, num_moves + num_switches)


def _adjust_search(battle: Battle, num_battles: int, search_time_ms: int):
    if battle.team_preview:
        return (
            max(1, FoulPlayConfig.team_preview_search_parallelism),
            max(25, FoulPlayConfig.team_preview_search_time_ms),
        )

    multiplier = 1.0
    turn = battle.turn or 0
    if turn >= 20:
        multiplier += 0.25
    if turn >= 30:
        multiplier += 0.25
    if (_get_hp_pct(battle.user.active) or 1) <= 0.25:
        multiplier += 0.25
    if (_get_hp_pct(battle.opponent.active) or 1) <= 0.25:
        multiplier += 0.25
    if _count_alive(battle.user) <= 2 or _count_alive(battle.opponent) <= 2:
        multiplier += 0.25
    if battle.time_remaining is not None:
        if battle.time_remaining <= 30:
            multiplier *= 0.5
        elif battle.time_remaining <= 60:
            multiplier *= 0.75

    branching = _estimate_branching_factor(battle)
    battle_multiplier = 1.0
    if branching <= 2:
        multiplier *= 0.70
        battle_multiplier = 0.70
    elif branching <= 3:
        multiplier *= 0.85
        battle_multiplier = 0.85
    elif branching >= 8:
        multiplier *= 1.25
        battle_multiplier = 1.20
    elif branching >= 6:
        multiplier *= 1.15
        battle_multiplier = 1.10

    return (
        max(1, int(num_battles * battle_multiplier)),
        max(25, int(search_time_ms * min(max(multiplier, 0.5), 2.0))),
    )


def _policy_confidence_ratio(policy: list[tuple[str, float]]) -> float:
    if len(policy) < 2 or policy[1][1] <= 0:
        return float("inf")
    return policy[0][1] / policy[1][1]


def find_best_move_with_policy(battle: Battle) -> tuple[str, list[tuple[str, float]]]:
    battle = deepcopy(battle)
    if battle.team_preview:
        battle.user.active = battle.user.reserve.pop(0)
        battle.opponent.active = battle.opponent.reserve.pop(0)

    num_battles, search_time_ms = battle.mode.search_params(battle)
    num_battles, search_time_ms = _adjust_search(battle, num_battles, search_time_ms)
    battles = battle.mode.prepare_battles(battle, num_battles)
    logger.info("Searching for a move using MCTS...")
    logger.info("Sampling %s battles at %sms each", num_battles, search_time_ms)

    results = _run_mcts_batch(battles, search_time_ms)
    final_policy = compute_final_policy(results)

    if (
        not battle.team_preview
        and _policy_confidence_ratio(final_policy) < 1.15
        and (battle.time_remaining is None or battle.time_remaining > 30)
    ):
        logger.info("Low-confidence policy; running one extra search pass")
        results += _run_mcts_batch(battles, max(25, int(search_time_ms * 0.5)))
        final_policy = compute_final_policy(results)

    final_policy = _apply_opponent_tendency_bias(battle, final_policy)
    resolved_risk = _resolve_risk_mode(battle)
    choice = select_move_from_policy(
        final_policy, resolved_risk, _configured_risk_mode()
    )
    logger.info("Choice: {}".format(choice))
    return choice, final_policy


def find_best_move(battle: Battle) -> str:
    return find_best_move_with_policy(battle)[0]
