# Foul Play

A Pokémon Showdown battle bot powered by [poke-engine](https://github.com/pmariglia/poke-engine).

This repository tracks [`pmariglia/foul-play`](https://github.com/pmariglia/foul-play) and keeps a small set of local features on top. The local changes are intentionally isolated so upstream updates are easier to merge.

![CI](https://github.com/HVS13/foul-play-bot/actions/workflows/ci.yml/badge.svg)

## Requirements

- Python 3.11+
- Rust when `poke-engine` must be built locally

Install dependencies:

```bash
pip install -r requirements.txt
```

For development:

```bash
pip install -r requirements-dev.txt
```

## Configuration

Run `python run.py --help` for the authoritative option list.

Required unless supplied by `--config`:

- `--websocket-uri`, such as `ps`, `pokemonshowdown`, `local`, or a full websocket URI
- `--ps-username`
- `--bot-mode`: `challenge_user`, `accept_challenge`, `search_ladder`, or `resume_battle`
- `--pokemon-format`, for example `gen9randombattle`

`--ps-password` is optional. When omitted, the upstream guest-login flow is used.

Important optional settings:

- `--config PATH`: TOML or JSON configuration file; explicit CLI arguments override file values
- `--ps-avatar`
- `--user-to-challenge`
- `--smogon-stats-format`
- `--search-time-ms` (default `100`)
- `--search-parallelism` (default `1`)
- `--team-preview-search-time-ms`
- `--team-preview-search-parallelism`
- `--search-threads` (default `1`)
- `--auto-parallelism` / `--no-auto-parallelism`
- `--parallelism-cap` (default `8`)
- `--run-count` (default `1`)
- `--team-name`
- `--team-list`
- `--save-replay`: `always`, `never`, `on_loss`, or `on_win`
- `--battle-timer`: `on`, `off`, or `none`
- `--suggest-only` / `--no-suggest-only`
- `--room-name`
- `--battle-tag` or `--battle-url` for `resume_battle`
- `--risk-mode`: `auto`, `safe`, `balanced`, or `aggressive`
- `--summary-path`
- `--summary-json-path`
- `--reconnect-retries` (default `5`)
- `--reconnect-backoff-seconds` (default `1.0`)
- `--reconnect-max-backoff-seconds` (default `30.0`)
- `--log-level`
- `--log-to-file` / `--no-log-to-file`

### Config files

Example `config.toml`:

```toml
websocket_uri = "ps"
ps_username = "My Username"
ps_password = "sekret"
bot_mode = "search_ladder"
pokemon_format = "gen9randombattle"

risk_mode = "auto"
auto_parallelism = true
parallelism_cap = 6
summary_path = "logs/battle_summary.txt"
summary_json_path = "logs/battle_summary.jsonl"
```

CLI values take precedence:

```bash
python run.py --config config.toml --risk-mode aggressive
```

## Local features in this repo

### Resume an active battle

Use the same Pokémon Showdown account that is already in the battle:

```bash
python run.py \
  --websocket-uri ps \
  --ps-username 'My Username' \
  --ps-password sekret \
  --bot-mode resume_battle \
  --pokemon-format gen9ou \
  --battle-tag battle-gen9ou-123456
```

A full battle URL also works:

```bash
python run.py \
  --websocket-uri ps \
  --ps-username 'My Username' \
  --ps-password sekret \
  --bot-mode resume_battle \
  --pokemon-format gen9ou \
  --battle-url https://play.pokemonshowdown.com/battle-gen9ou-123456
```

The current battle tag is persisted to `logs/last_battle_tag.txt`. If the websocket disconnects, the client reconnects, rejoins the battle, and attempts to rebuild state automatically.

### Risk modes

`--risk-mode balanced` stays close to upstream move selection. Other modes change how the final MCTS policy is sampled:

- `safe`: favors the highest-weight move
- `balanced`: normal near-best exploration
- `aggressive`: considers a wider near-best set
- `auto`: switches among those behaviors based on remaining Pokémon and HP position

### Suggest-only mode

Add `--suggest-only` to calculate and log decisions without sending `/choose`, `/switch`, or `/team` commands. Suggestions include short tags such as `attack`, `ko`, `setup`, `pivot`, and `heal` when available.

### Search and telemetry

The local search layer adds:

- CPU-based auto parallelism with a configurable cap
- dynamic search effort based on turn, HP, remaining Pokémon, timer pressure, and branching factor
- an extra search pass when the leading policy choices are unusually close
- persistent MCTS process-pool reuse with recovery after `BrokenProcessPool`
- light opponent switch/protect tendency tracking and policy bias

Battle summaries can be appended as text with `--summary-path` and as JSONL with `--summary-json-path`. JSON summaries include decision logs, search timing, result information, reconnect count, replay metadata, and opponent tendency counters.

## Common runs

Search the ladder:

```bash
python run.py \
  --websocket-uri ps \
  --ps-username 'My Username' \
  --ps-password sekret \
  --bot-mode search_ladder \
  --pokemon-format gen9randombattle
```

Accept challenges:

```bash
python run.py \
  --websocket-uri ps \
  --ps-username 'My Username' \
  --ps-password sekret \
  --bot-mode accept_challenge \
  --pokemon-format gen9randombattle \
  --room-name lobby
```

Challenge another user:

```bash
python run.py \
  --websocket-uri ps \
  --ps-username 'My Username' \
  --ps-password sekret \
  --bot-mode challenge_user \
  --user-to-challenge 'Opponent Name' \
  --pokemon-format gen9randombattle
```

## Docker

Build with the Makefile:

```bash
make docker
```

Or for a specific generation:

```bash
make docker GEN=gen4
```

Example:

```bash
docker run --rm --network host foul-play:latest \
  --websocket-uri ps \
  --ps-username 'My Username' \
  --ps-password sekret \
  --bot-mode search_ladder \
  --pokemon-format gen9randombattle
```

## Engine

This project uses [poke-engine](https://github.com/pmariglia/poke-engine) for battle search. See the [poke-engine documentation](https://poke-engine.readthedocs.io/en/latest/) for engine details.

To rebuild the engine for another generation:

```bash
pip uninstall -y poke-engine && \
pip install -v --force-reinstall --no-cache-dir poke-engine \
  --config-settings="build-args=--features poke-engine/<GENERATION> --no-default-features"
```

Or:

```bash
make poke_engine GEN=<generation>
```

## Updating from upstream

This repository is an independent copy, not a GitHub fork. Keep the original repository configured as an `upstream` remote.

One-time setup:

```bash
git clone https://github.com/HVS13/foul-play-bot.git
cd foul-play-bot
git remote add upstream https://github.com/pmariglia/foul-play.git
git fetch upstream
```

For each upstream update, use a sync branch:

```bash
git checkout main
git pull origin main
git checkout -b sync/upstream-YYYY-MM
git fetch upstream
git merge upstream/main
```

Resolve conflicts by keeping the current upstream architecture, then reapply only the local hook behavior. Most local-only logic now lives in `fp/custom_features.py`; configuration is in `fp/config.py`; policy behavior is in `fp/search/main.py`; resume/reconnect orchestration is in `fp/run_battle.py`.

Validate before merging:

```bash
ruff check
ruff format --check --diff
pytest tests
```

Then push the sync branch, open a PR into `main`, wait for CI, and merge it:

```bash
git push -u origin sync/upstream-YYYY-MM
```
