import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path


def load_summaries(path: str | Path) -> list[dict]:
    summaries = []
    with open(path, "r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "Invalid JSONL at line {}: {}".format(line_number, exc)
                ) from exc
            if isinstance(value, dict):
                summaries.append(value)
    return summaries


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _decision_confidence(decision: dict) -> float | None:
    if decision.get("confidence_ratio") is not None:
        return float(decision["confidence_ratio"])
    policy = decision.get("policy_top") or []
    if len(policy) < 2:
        return None
    top = float(policy[0].get("weight", 0) or 0)
    second = float(policy[1].get("weight", 0) or 0)
    if second <= 0:
        return None
    return top / second


def summarize(summaries: list[dict], username: str | None = None) -> dict:
    turns = []
    durations = []
    decision_times = []
    confidence_ratios = []
    risk_modes = Counter()
    decision_risk_modes = Counter()
    win_reasons = Counter()
    reconnects = 0
    room_renames = 0
    timer_pressure_decisions = 0
    total_decisions = 0
    wins = losses = ties = 0

    for battle in summaries:
        turns.append(int(battle.get("turns", 0) or 0))
        if battle.get("duration_seconds") is not None:
            durations.append(float(battle["duration_seconds"]))
        reconnects += int(battle.get("reconnect_count", 0) or 0)
        room_renames += int(battle.get("room_rename_count", 0) or 0)
        risk_modes[str(battle.get("risk_mode") or "unknown")] += 1
        win_reasons[str(battle.get("win_reason") or "unknown")] += 1

        winner = battle.get("winner")
        if username:
            if winner is None:
                ties += 1
            elif str(winner).casefold() == username.casefold():
                wins += 1
            else:
                losses += 1

        decisions = battle.get("decision_log") or []
        total_decisions += len(decisions)
        for decision in decisions:
            if decision.get("search_time_ms") is not None:
                decision_times.append(float(decision["search_time_ms"]))
            ratio = _decision_confidence(decision)
            if ratio is not None:
                confidence_ratios.append(ratio)
            configured_risk = decision.get("configured_risk_mode")
            if configured_risk:
                decision_risk_modes[str(configured_risk)] += 1
            time_remaining = decision.get("time_remaining")
            if time_remaining is not None and float(time_remaining) <= 30:
                timer_pressure_decisions += 1

    low_confidence = sum(1 for ratio in confidence_ratios if ratio <= 1.15)
    report = {
        "battles": len(summaries),
        "decisions": total_decisions,
        "avg_turns": round(statistics.mean(turns), 2) if turns else None,
        "avg_duration_seconds": (
            round(statistics.mean(durations), 2) if durations else None
        ),
        "search_time_ms": {
            "avg": round(statistics.mean(decision_times), 2)
            if decision_times
            else None,
            "median": (
                round(statistics.median(decision_times), 2) if decision_times else None
            ),
            "p95": (
                round(_percentile(decision_times, 0.95), 2) if decision_times else None
            ),
            "max": round(max(decision_times), 2) if decision_times else None,
        },
        "confidence": {
            "observations": len(confidence_ratios),
            "low_confidence_count": low_confidence,
            "low_confidence_pct": (
                round(100 * low_confidence / len(confidence_ratios), 2)
                if confidence_ratios
                else None
            ),
            "median_top1_top2_ratio": (
                round(statistics.median(confidence_ratios), 3)
                if confidence_ratios
                else None
            ),
        },
        "reconnects": reconnects,
        "room_renames": room_renames,
        "timer_pressure_decisions": timer_pressure_decisions,
        "risk_modes": dict(risk_modes),
        "decision_risk_modes": dict(decision_risk_modes),
        "win_reasons": dict(win_reasons),
    }

    if username:
        decisive = wins + losses
        report["record"] = {
            "username": username,
            "wins": wins,
            "losses": losses,
            "ties": ties,
            "win_rate_pct": round(100 * wins / decisive, 2) if decisive else None,
        }
    return report


def format_text(report: dict) -> str:
    lines = [
        "Foul Play telemetry report",
        "Battles: {} | Decisions: {}".format(report["battles"], report["decisions"]),
    ]

    record = report.get("record")
    if record:
        lines.append(
            "Record for {username}: {wins}W {losses}L {ties}T | win rate: {rate}".format(
                username=record["username"],
                wins=record["wins"],
                losses=record["losses"],
                ties=record["ties"],
                rate=(
                    "n/a"
                    if record["win_rate_pct"] is None
                    else "{}%".format(record["win_rate_pct"])
                ),
            )
        )

    search = report["search_time_ms"]
    lines.append(
        "Search ms: avg {avg} | median {median} | p95 {p95} | max {max}".format(
            **{key: "n/a" if value is None else value for key, value in search.items()}
        )
    )
    confidence = report["confidence"]
    low_pct = confidence["low_confidence_pct"]
    lines.append(
        "Low-confidence decisions (top1/top2 <= 1.15): {count}/{observations} ({pct})".format(
            count=confidence["low_confidence_count"],
            observations=confidence["observations"],
            pct="n/a" if low_pct is None else "{}%".format(low_pct),
        )
    )
    lines.append(
        "Recovery: {} reconnects | {} room renames".format(
            report["reconnects"], report["room_renames"]
        )
    )
    lines.append(
        "Timer-pressure decisions (<=30s): {}".format(
            report["timer_pressure_decisions"]
        )
    )
    lines.append("Risk modes by battle: {}".format(report["risk_modes"]))
    if report["decision_risk_modes"]:
        lines.append("Risk modes by decision: {}".format(report["decision_risk_modes"]))
    lines.append("Win reasons: {}".format(report["win_reasons"]))
    if report["avg_turns"] is not None:
        lines.append("Average turns: {}".format(report["avg_turns"]))
    if report["avg_duration_seconds"] is not None:
        lines.append("Average duration: {}s".format(report["avg_duration_seconds"]))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize Foul Play JSONL telemetry")
    parser.add_argument("path", nargs="?", default="logs/battle_summary.jsonl")
    parser.add_argument("--username", default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        raise SystemExit("Telemetry file not found: {}".format(path))

    report = summarize(load_summaries(path), username=args.username)
    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_text(report))


if __name__ == "__main__":
    main()
