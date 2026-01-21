# CodexGPT Agent Guide

## Project summary
- Foul Play is a Pokemon Showdown battle bot driven by poke-engine search.
- Entry points: `run.py` for CLI, `config.py` for options/validation.
- Battle flow: `fp/run_battle.py`, state in `fp/battle.py`, protocol parsing in `fp/battle_modifier.py`.
- PS websocket integration: `fp/websocket_client.py`.

## Common commands
- Show options: `python run.py --help`
- Search ladder:
  ```bash
  python run.py --websocket-uri wss://sim3.psim.us/showdown/websocket \
  --ps-username 'My Username' --ps-password sekret \
  --bot-mode search_ladder --pokemon-format gen9randombattle
  ```
- Resume an in-progress battle (logs in as the player account):
  ```bash
  python run.py --websocket-uri wss://sim3.psim.us/showdown/websocket \
  --ps-username 'My Username' --ps-password sekret \
  --bot-mode resume_battle --pokemon-format gen9ou \
  --battle-tag battle-gen9ou-123456
  ```
- Suggest-only (no moves sent): add `--suggest-only`.

## Sanity checks
- Quick compile check: `python -m compileall .`
- Run tests if needed: `pytest`

## Update from upstream
- Fetch and merge: `git fetch upstream && git merge upstream/main`
- Push your fork: `git push origin main`

## Documentation expectations
- Update `README.md` when adding CLI flags, bot modes, or behavioral changes.
