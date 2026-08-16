# CodexGPT Agent Guide

## Product goal
Foul Play is a Pokémon Showdown battle bot driven by poke-engine search. This repository tracks `pmariglia/foul-play` and adds a small local layer for recovery, risk/search behavior, telemetry, and a live dashboard/overlay.

The 80/20 rule for this repo: prefer a small, reliable feature that improves battle operation or decision visibility over a broad framework or speculative abstraction.

## Shared direction with upstream
Treat upstream as the source of truth for competitive battle intelligence: generation mechanics, format support, hidden-state/set inference, battle modes, sampling, and poke-engine search. The local project should complement that work rather than fork it into a competing engine.

Local differentiation should concentrate on three areas:
1. **Operational reliability** around Pokémon Showdown transport and battle-session recovery.
2. **Observability** that makes upstream search, uncertainty, latency, and outcomes measurable.
3. **Optional interfaces** such as the dashboard/overlay that consume the same engine state without owning battle logic.

When choosing work, prefer in order:
- correctness/protocol bugs that can invalidate otherwise-good engine decisions;
- measurement needed to evaluate real battle performance;
- small usability improvements that do not distort battle logic;
- decision heuristics only after telemetry shows a specific recurring weakness.

Do not replace or broadly rewrite upstream search/sampling architecture merely to make local customization easier. If a local idea is generally useful to Foul Play, keep its implementation small enough that it could plausibly be upstreamed or removed when upstream gains an equivalent feature.

## Architecture
- Entry point: `run.py`.
- CLI/configuration: `fp/config.py`.
- Orchestration: `fp/main.py`.
- Battle loop and attach/reconnect recovery: `fp/run_battle.py`.
- Battle state: `fp/battle/state.py`.
- Protocol parsing: `fp/battle/protocol.py`.
- Battle modes and upstream uncertainty sampling: `fp/modes/`.
- Search execution and policy selection: `fp/search/main.py`.
- Pokémon Showdown transport: `fp/websocket_client.py`.
- Local-only behavior: `fp/custom/`.
  - `decisions.py`: shared decision classification and search-result model.
  - `events.py`: thread-safe event/state stream for interfaces.
  - `opponent_model.py`: observed opponent behavior counters.
  - `telemetry.py`: persistence, summaries, and decision logs.
  - `dashboard.py`: optional local HTTP server.
- Browser UI: `fp/gui/dashboard.html` and `fp/gui/overlay.html`.

## Locked design decisions
- Keep upstream-facing edits thin. Put local-only logic under `fp/custom/` whenever practical.
- The dashboard consumes events. It must not call or own battle/search logic.
- The MVP dashboard is read-only. Controls/authentication/remote hosting are deliberately out of scope.
- The dashboard uses the Python standard library only. Do not add a web framework without a concrete need.
- The default GUI host is loopback-only (`127.0.0.1`). Treat non-loopback binding as explicitly unsafe unless authentication is added later.
- Explicit resume and reconnect recovery share one `attach_to_battle()` state-rebuild path.
- Never automatically resend a battle choice after a websocket reconnect. Rebuild current state and calculate against the latest request instead.
- Pokémon Showdown room renames are transport aliases. Resolve commands to the canonical room and update the active session tag without moving room-routing logic into the battle engine.
- Preserve upstream public search helpers (`find_best_move`, `find_best_move_with_policy`) for compatibility; new local consumers use `find_best_move_result`.

## Current MVP
IN:
- Existing CLI battle modes and local risk/reconnect/summary features.
- Shared decision/search result model.
- Shared event stream.
- Local dashboard at `/` and transparent browser/OBS overlay at `/overlay` when `--gui` is enabled.
- Live battle state, recommendations, confidence, search timing, risk mode, opponent counters, and recent events.

OUT for now:
- Native desktop packaging.
- GUI controls that can send battle actions.
- Authentication and internet-facing hosting.
- Historical analytics/charts beyond existing JSONL summaries.
- Replacing upstream MCTS or battle-mode sampling architecture.

## Common commands
- Show options: `python run.py --help`
- Search ladder:
  ```bash
  python run.py --websocket-uri ps \
  --ps-username 'My Username' --ps-password sekret \
  --bot-mode search_ladder --pokemon-format gen9randombattle
  ```
- Add `--gui` for the local dashboard and overlay.
- Resume an in-progress battle:
  ```bash
  python run.py --websocket-uri ps \
  --ps-username 'My Username' --ps-password sekret \
  --bot-mode resume_battle --pokemon-format gen9ou \
  --battle-tag battle-gen9ou-123456
  ```
- Suggest-only: add `--suggest-only`; battle choices are calculated and displayed but not sent.

## Done criteria for local changes
Before merging:
- `python -m compileall -q fp`
- `ruff check`
- `ruff format --check --diff`
- `pytest tests`
- Any new recovery/search/interface behavior has focused regression coverage.
- README and this file reflect user-visible or architectural changes.

## Updating from upstream
Configure the original repository once:
```bash
git remote add upstream https://github.com/pmariglia/foul-play.git
git fetch upstream
```

For each update, use a branch rather than merging directly into `main`:
```bash
git checkout main
git pull origin main
git checkout -b sync/upstream-YYYY-MM
git fetch upstream
git merge upstream/main
# keep upstream architecture, then reapply the smallest local hooks
git push -u origin sync/upstream-YYYY-MM
```

Open a PR into `main`, wait for CI, then merge it. Record any new architectural decision here so future sessions do not rediscover it.