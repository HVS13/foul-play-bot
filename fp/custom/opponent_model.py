from fp.battle.helpers import normalize_name
from fp.custom.decisions import PROTECT_MOVE_IDS


def update_opponent_tendencies(battle, msg: str) -> None:
    opponent_side = getattr(battle.opponent, "name", None)
    if not opponent_side:
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
        if not actor.startswith(opponent_side):
            continue

        if action == "switch":
            tendencies["switches"] += 1
            tendencies["actions"] += 1
        elif action == "move":
            tendencies["moves"] += 1
            tendencies["actions"] += 1
            if len(split_line) > 3 and normalize_name(split_line[3]) in PROTECT_MOVE_IDS:
                tendencies["protects"] += 1
