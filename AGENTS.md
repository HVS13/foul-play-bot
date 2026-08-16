# CodexGPT Agent Guide

## Project summary
- Foul Play is a Pokemon Showdown battle bot driven by poke-engine search.
- This repository tracks `pmariglia/foul-play` plus local custom features.
- Entry point: `run.py`; CLI/configuration: `fp/config.py`; orchestration: `fp/main.py`.
- Battle flow: `fp/run_battle.py`; state: `fp/battle/state.py`; protocol parsing: `fp/battle/protocol.py`.
- Battle modes live in `fp/modes/`; custom QoL/telemetry helpers live in `fp/custom_features.py`.
- PS websocket integration: `fp/websocket_client.py`.

## Common commands
- Show options: `python run.py --help`
- Search ladder:
  ```bash
  python run.py --websocket-uri ps \
  --ps-username 'My Username' --ps-password sekret \
  --bot-mode search_ladder --pokemon-format gen9randombattle
  ```
- Resume an in-progress battle:
  ```bash
  python run.py --websocket-uri ps \
  --ps-username 'My Username' --ps-password sekret \
  --bot-mode resume_battle --pokemon-format gen9ou \
  --battle-tag battle-gen9ou-123456
  ```
- Suggest-only: add `--suggest-only`; battle choices are logged but not sent.

## Sanity checks
- Compile: `python -m compileall -q fp`
- Lint: `ruff check`
- Format check: `ruff format --check --diff`
- Tests: `pytest tests`

## Update from upstream
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
# resolve conflicts by keeping upstream architecture and reapplying local hooks
git push -u origin sync/upstream-YYYY-MM
```
Open a PR into `main`, wait for CI, then merge it.

## Local customization boundary
Prefer putting new local-only behavior in `fp/custom_features.py` or another isolated local module. Keep edits to upstream files small and hook-like. This reduces future upstream merge conflicts.

## Documentation expectations
Update `README.md` when adding CLI flags, bot modes, sync instructions, or user-visible behavior.
