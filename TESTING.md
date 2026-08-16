# Testing Foul Play

This runbook is intentionally short. The goal is to verify the interface quickly, then collect enough real battle evidence to decide what is worth optimizing next.

## 1. Test the GUI without Pokémon Showdown

Install the normal dependencies, then run:

```bash
python -m fp.custom.demo
```

This starts the real local dashboard server, opens the dashboard in your browser, and feeds it synthetic battle/search/reconnect events.

- Dashboard: `http://127.0.0.1:8765/`
- Overlay: `http://127.0.0.1:8765/overlay`

Useful options:

```bash
python -m fp.custom.demo --loop
python -m fp.custom.demo --no-open
python -m fp.custom.demo --port 8766
```

The demo does not log in to Pokémon Showdown and cannot send battle commands.

## 2. Run a real test session

Example random-battle session:

```bash
python run.py \
  --websocket-uri ps \
  --ps-username 'My Username' \
  --ps-password sekret \
  --bot-mode search_ladder \
  --pokemon-format gen9randombattle \
  --risk-mode auto \
  --auto-parallelism \
  --gui \
  --run-count 5
```

When `--gui` is enabled, battle JSON telemetry is automatically appended to:

```text
logs/battle_summary.jsonl
```

You can still override the destination with `--summary-json-path PATH`.

For a first live test, `--suggest-only` is useful when attaching to a battle because it lets you inspect recommendations without sending battle choices.

## 3. Summarize the session

After one or more battles:

```bash
python -m fp.custom.report --username 'My Username'
```

The report includes:

- wins, losses, ties, and win rate
- average battle turns and duration
- average, median, p95, and maximum search time
- number and percentage of low-confidence decisions
- reconnect count
- risk-mode usage
- win/termination reasons

Low confidence currently means the top policy weight is no more than 1.15x the second-best weight. This is a diagnostic signal, not a claim that the selected move was wrong.

Machine-readable output:

```bash
python -m fp.custom.report --username 'My Username' --json
```

Analyze another telemetry file:

```bash
python -m fp.custom.report path/to/session.jsonl --username 'My Username'
```

## What to look for first

Use the first real session to answer four questions before changing heuristics again:

1. Is p95 search latency comfortably below the battle timer pressure you actually encounter?
2. Are low-confidence decisions common enough that extra search is worth its CPU cost?
3. Does reconnect/attach preserve the battle and continue from the correct current request?
4. Do `auto` risk choices look sensible in positions where you are clearly ahead or behind?

Do not tune opponent-tendency weights from only a handful of battles. Collect enough sessions that the behavior is repeated rather than anecdotal.
