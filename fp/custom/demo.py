import argparse
import time
import webbrowser
from types import SimpleNamespace

from fp.custom.dashboard import start_dashboard, stop_dashboard
from fp.custom.events import publish_event


def _pokemon(name: str, hp_pct: float, status: str = ""):
    max_hp = 100
    return SimpleNamespace(
        name=name,
        nickname=None,
        hp=round(max_hp * hp_pct),
        max_hp=max_hp,
        status=status,
        item=None,
    )


def build_demo_battle():
    return SimpleNamespace(
        battle_tag="battle-demo-0001",
        pokemon_format="gen9randombattle",
        turn=1,
        time_remaining=150,
        wait=False,
        force_switch=False,
        user=SimpleNamespace(name="p1", active=_pokemon("greattusk", 1.0)),
        opponent=SimpleNamespace(
            name="p2",
            account_name="Demo Opponent",
            active=_pokemon("kingambit", 1.0),
        ),
        opponent_tendencies={
            "actions": 0,
            "moves": 0,
            "switches": 0,
            "protects": 0,
        },
    )


def _decision(
    choice: str,
    policy: list[tuple[str, float]],
    confidence_ratio: float,
    risk_mode: str,
    total_search_time_ms: int,
    sampled_states: int,
):
    return {
        "choice": choice,
        "policy": [
            {"move": move, "weight": weight, "tags": []} for move, weight in policy
        ],
        "confidence_ratio": confidence_ratio,
        "sampled_states": sampled_states,
        "search_passes": 1,
        "search_time_per_state_ms": max(1, total_search_time_ms // sampled_states),
        "total_search_time_ms": total_search_time_ms,
        "risk_mode": risk_mode,
    }


def run_demo(interval: float = 2.0) -> None:
    battle = build_demo_battle()
    publish_event("connection_open")
    publish_event("battle_started", battle)

    steps = [
        {
            "user": ("greattusk", 0.83),
            "opponent": ("kingambit", 0.61),
            "policy": [
                ("earthquake", 0.472),
                ("stealthrock", 0.298),
                ("switch dragapult", 0.161),
            ],
            "choice": "earthquake",
            "confidence": 1.58,
            "risk": "balanced",
            "search_ms": 426,
            "states": 8,
        },
        {
            "user": ("greattusk", 0.48),
            "opponent": ("rotomwash", 0.76),
            "policy": [
                ("switch dragapult", 0.391),
                ("knockoff", 0.362),
                ("rapidspin", 0.151),
            ],
            "choice": "switch dragapult",
            "confidence": 1.08,
            "risk": "aggressive",
            "search_ms": 703,
            "states": 12,
        },
        {
            "user": ("dragapult", 0.91),
            "opponent": ("rotomwash", 0.32),
            "policy": [
                ("dracometeor", 0.621),
                ("shadowball", 0.244),
                ("uturn", 0.089),
            ],
            "choice": "dracometeor",
            "confidence": 2.55,
            "risk": "safe",
            "search_ms": 318,
            "states": 6,
        },
    ]

    for index, step in enumerate(steps, start=1):
        battle.turn = index + 4
        battle.time_remaining = 145 - index * 17
        battle.user.active = _pokemon(*step["user"])
        battle.opponent.active = _pokemon(*step["opponent"])
        battle.opponent_tendencies["actions"] += 3
        battle.opponent_tendencies["moves"] += 2
        if index == 2:
            battle.opponent_tendencies["switches"] += 1
        if index == 3:
            battle.opponent_tendencies["protects"] += 1

        publish_event("battle_updated", battle)
        publish_event("search_started", battle)
        time.sleep(min(0.35, interval / 3))
        result = _decision(
            step["choice"],
            step["policy"],
            step["confidence"],
            step["risk"],
            step["search_ms"],
            step["states"],
        )
        publish_event(
            "decision_ready",
            battle,
            result=result,
            telemetry={
                "turn": battle.turn,
                "decision": step["choice"],
                "search_time_ms": step["search_ms"],
            },
        )
        time.sleep(interval)

    publish_event("connection_lost", battle, reason="demo disconnect")
    time.sleep(min(1.0, interval))
    publish_event("reconnected", battle)
    publish_event("battle_attached", battle)
    time.sleep(interval)
    publish_event("battle_finished", battle, winner="Demo User", win_reason="normal")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Offline Foul Play dashboard smoke demo"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one demo sequence and exit; useful for automated smoke tests",
    )
    args = parser.parse_args()

    url = start_dashboard(args.host, args.port)
    if not args.no_open:
        webbrowser.open(url)

    print("Dashboard: {}".format(url))
    print("Overlay: {}/overlay".format(url))
    if not args.once:
        print("Press Ctrl+C to stop.")

    demo_interval = max(0.0, args.interval)
    try:
        while True:
            run_demo(demo_interval)
            if args.once:
                break
            if not args.loop:
                while True:
                    time.sleep(3600)
            time.sleep(max(0.2, demo_interval))
    except KeyboardInterrupt:
        pass
    finally:
        stop_dashboard()


if __name__ == "__main__":
    main()
