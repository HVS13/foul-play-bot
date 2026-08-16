import atexit
import logging
import random
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from copy import deepcopy

from fp.battle.state import Battle
from fp.config import FoulPlayConfig, RiskModes
from fp.custom.decisions import SearchResult, analyze_decision
from fp.custom.events import publish_event
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


def _normalize_policy(policy: list[tuple[str, float]]) -> list[tuple[str, float]]:
    total = sum(weight for _, weight in policy)
    if total <= 0:
        return policy
    return [(move, weight / total) for move, weight in policy]


def compute_final_policy(
    mcts_results: list[tuple[MctsResult, float, int]],
) -> list[tuple[str, float]]:
    final_policy = {}
    for mcts_result, sample_chance, index in mcts_results:
        if mcts_result.total_visits <= 0 or not mcts_result.side_one:
            continue
        this_policy = max(mcts_result.side_one, key=lambda x: x.visits)
        logger.info(
            "Policy {}: {} visited {}% avg_score={} sample_chance_multiplier={}".format(
                index,
                this_policy.move_choice,
                round(100 * this_policy.visits / mcts_result.total_visits, 2),
                round(this_policy.total_score / max(this_policy.visits, 1), 3),
                round(sample_chance, 3),
            )
        )
        for option in mcts_result.side_one:
            final_policy[option.move_choice] = final_policy.get(
                option.move_choice, 0
            ) + (sample_chance * (option.visits / mcts_result.total_visits))

    sorted_policy = sorted(final_policy.items(), key=lambda x: x[1], reverse=True)
    return _normalize_policy(sorted_policy)


def _apply_opponent_tendency_bias(
    battle: Battle, final_policy: list[tuple[str, float]]
) -> list[tuple[str, float]]:
    tendencies = getattr(battle, "opponent_tendencies", None)
    if not tendencies or tendencies.get("actions", 0) < 8:
        return final_policy

    actions = tendencies.get("actions", 0)
    moves = tendencies.get("moves", 0)
    switch_rate = tendencies.get("switches", 0) / max(actions, 1)
    protect_rate = tendencies.get("protects", 0) / max(moves, 1)
    if switch_rate < 0.35 and protect_rate < 0.25:
        return final_policy

    adjusted = []
    for move, weight in final_policy:
        tags = set(analyze_decision(None, move).tags)
        multiplier = 1.0
        if switch_rate >= 0.45 and tags.intersection({"pivot", "setup", "status"}):
            multiplier += 0.08
        if protect_rate >= 0.30 and tags.intersection({"setup", "status", "switch"}):
            multiplier += 0.05
        adjusted.append((move, weight * multiplier))
    adjusted.sort(key=lambda x: x[1], reverse=True)
    adjusted = _normalize_policy(adjusted)
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
    return select_move_from_policy(policy, mode, _configured_risk_mode())


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
            for future, _, _ in futures:
                future.cancel()
            logger.warning("MCTS worker pool crashed; recreating pool")
            _reset_process_pool()
            if attempt == 1:
                raise
    raise RuntimeError("MCTS search failed without a result")


def _get_hp_pct(pokemon):
    if pokemon is None or pokemon.max_hp == 0:
        return None
    return pokemon.hp / pokemon.max_hp


def _count_alive(battler):
    alive = int(battler.active is not None and battler.active.is_alive())
    return alive + sum(1 for pokemon in battler.reserve if pokemon.is_alive())


def _adjust_search(battle: Battle, num_battles: int, search_time_ms: int):
    """Preserve the mode's breadth/depth plan and only shorten it for the timer."""
    if battle.team_preview:
        return (
            max(1, FoulPlayConfig.team_preview_search_parallelism),
            max(25, FoulPlayConfig.team_preview_search_time_ms),
        )

    time_scale = 1.0
    if battle.time_remaining is not None:
        if battle.time_remaining <= 30:
            time_scale = 0.5
        elif battle.time_remaining <= 60:
            time_scale = 0.75

    return max(1, num_battles), max(25, int(search_time_ms * time_scale))


def _allocate_search_pass_times(
    search_time_ms: int, allow_retry: bool
) -> tuple[int, int]:
    """Reserve retry time inside the original per-state budget."""
    search_time_ms = max(25, int(search_time_ms))
    if not allow_retry or search_time_ms < 100:
        return search_time_ms, 0

    retry_time_ms = max(25, int(search_time_ms * 0.20))
    first_pass_time_ms = search_time_ms - retry_time_ms
    if first_pass_time_ms < 25:
        return search_time_ms, 0
    return first_pass_time_ms, retry_time_ms


def _scale_result_weights(results, multiplier: float):
    if multiplier == 1.0:
        return results
    return [
        (mcts_result, sample_chance * multiplier, index)
        for mcts_result, sample_chance, index in results
    ]


def _policy_confidence_ratio(policy: list[tuple[str, float]]) -> float:
    if len(policy) < 2 or policy[1][1] <= 0:
        return float("inf")
    return policy[0][1] / policy[1][1]


def find_best_move_result(battle: Battle) -> SearchResult:
    battle = deepcopy(battle)
    if battle.team_preview:
        battle.user.active = battle.user.reserve.pop(0)
        battle.opponent.active = battle.opponent.reserve.pop(0)

    num_battles, search_time_ms = battle.mode.search_params(battle)
    num_battles, search_time_ms = _adjust_search(battle, num_battles, search_time_ms)
    battles = battle.mode.prepare_battles(battle, num_battles)

    allow_retry = not battle.team_preview and (
        battle.time_remaining is None or battle.time_remaining > 30
    )
    first_pass_time_ms, retry_time_ms = _allocate_search_pass_times(
        search_time_ms, allow_retry
    )

    logger.info("Searching for a move using MCTS...")
    logger.info(
        "Sampling %s battles at %sms each (budget %sms/state)",
        num_battles,
        first_pass_time_ms,
        search_time_ms,
    )
    publish_event(
        "search_started",
        battle,
        sampled_states=len(battles),
        search_time_per_state_ms=first_pass_time_ms,
        search_budget_per_state_ms=search_time_ms,
        retry_reserved_ms=retry_time_ms,
    )

    started = time.perf_counter()
    results = _run_mcts_batch(battles, first_pass_time_ms)
    final_policy = compute_final_policy(results)
    search_passes = 1
    used_time_per_state_ms = first_pass_time_ms

    if retry_time_ms and _policy_confidence_ratio(final_policy) < 1.15:
        logger.info(
            "Low-confidence policy; using reserved %sms retry budget", retry_time_ms
        )
        retry_results = _run_mcts_batch(battles, retry_time_ms)
        retry_weight = retry_time_ms / first_pass_time_ms
        results += _scale_result_weights(retry_results, retry_weight)
        final_policy = compute_final_policy(results)
        search_passes = 2
        used_time_per_state_ms += retry_time_ms

    final_policy = _apply_opponent_tendency_bias(battle, final_policy)
    resolved_risk = _resolve_risk_mode(battle)
    choice = select_move_from_policy(
        final_policy, resolved_risk, _configured_risk_mode()
    )
    total_search_time_ms = int((time.perf_counter() - started) * 1000)
    logger.info("Choice: {}".format(choice))
    return SearchResult(
        choice=choice,
        policy=final_policy,
        confidence_ratio=_policy_confidence_ratio(final_policy),
        sampled_states=len(battles),
        search_passes=search_passes,
        search_time_per_state_ms=used_time_per_state_ms,
        total_search_time_ms=total_search_time_ms,
        risk_mode=resolved_risk.name,
    )


def find_best_move_with_policy(battle: Battle) -> tuple[str, list[tuple[str, float]]]:
    result = find_best_move_result(battle)
    return result.choice, result.policy


def find_best_move(battle: Battle) -> str:
    return find_best_move_result(battle).choice
