# Foul Play

A Pokémon Showdown battle bot powered by [poke-engine](https://github.com/pmariglia/poke-engine).

This repository tracks [`pmariglia/foul-play`](https://github.com/pmariglia/foul-play) and keeps a deliberately small local layer for recovery, search/risk behavior, telemetry, and a live dashboard/overlay.

![CI](https://github.com/HVS13/foul-play-bot/actions/workflows/ci.yml/badge.svg)

## Requirements

- Python 3.11+
- Rust when `poke-engine` must be built locally

```bash
pip install -r requirements.txt
```

For development:

```bash
pip install -r requirements-dev.txt
```

## Quick start

Search the ladder:

```bash
python run.py \
  --websocket-uri ps \
  --ps-username 'My Username' \
  --ps-password sekret \
  --bot-mode search_ladder \
  --pokemon-format gen9randombattle
```

Add `--gui` to start the local dashboard:

```bash
python run.py \
  --websocket-uri ps \
  --ps-username 'My Username' \
  --ps-password sekret \
  --bot-mode search_ladder \
  --pokemon-format gen9randombattle \
  --gui
```

Open:

- Dashboard: `http://127.0.0.1:8765/`
- Browser/OBS overlay: `http://127.0.0.1:8765/overlay`

The GUI is read-only and uses only the Python standard library. It is intentionally bound to `127.0.0.1` by default. There is no authentication, so do not expose it to a network unless you understand that tradeoff.

## Configuration

Run `python run.py --help` for the authoritative option list.

Required unless supplied by `--config`:

- `--websocket-uri`: `ps`, `pokemonshowdown`, `local`, or a full websocket URI
- `--ps-username`
- `--bot-mode`: `challenge_user`, `accept_challenge`, `search_ladder`, or `resume_battle`
- `--pokemon-format`, for example `gen9randombattle`

`--ps-password` is optional. When omitted, the upstream guest-login flow is used.

Important optional settings:

- `--config PATH`: TOML or JSON; explicit CLI arguments override file values
- `--search-time-ms` (default `100`)
- `--search-parallelism` (default `1`)
- `--search-threads` (default `1`)
- `--team-preview-search-time-ms`
- `--team-preview-search-parallelism`
- `--auto-parallelism` / `--no-auto-parallelism`
- `--parallelism-cap` (default `8`)
- `--risk-mode`: `auto`, `safe`, `balanced`, or `aggressive`
- `--battle-timer`: `on`, `off`, or `none`
- `--suggest-only` / `--no-suggest-only`
- `--save-replay`: `always`, `never`, `on_loss`, or `on_win`
- `--summary-path`
- `--summary-json-path`
- `--reconnect-retries` (default `5`)
- `--reconnect-backoff-seconds` (default `1.0`)
- `--reconnect-max-backoff-seconds` (default `30.0`)
- `--gui` / `--no-gui`
- `--gui-host` (default `127.0.0.1`)
- `--gui-port` (default `8765`)
- `--run-count` (default `1`)
- `--team-name`
- `--team-list`
- `--room-name`
- `--battle-tag` or `--battle-url` for `resume_battle`
- `--log-level`
- `--log-to-file` / `--no-log-to-file`

Unknown keys in config files are rejected instead of being silently ignored.

### Config file example

```toml
websocket_uri = "ps"
ps_username = "My Username"
ps_password = "sekret"
bot_mode = "search_ladder"
pokemon_format = "gen9randombattle"

risk_mode = "auto"
auto_parallelism = true
parallelism_cap = 6
search_threads = 2

gui = true
gui_host = "127.0.0.1"
gui_port = 8765

summary_path = "logs/battle_summary.txt"
summary_json_path = "logs/battle_summary.jsonl"
```

CLI values take precedence:

```bash
python run.py --config config.toml --risk-mode aggressive
```

## Dashboard and overlay

The dashboard receives the same internal events used by telemetry. It does not own battle or search logic.

It shows:

- current battle, turn, timer, and active Pokémon HP
- top move recommendations and policy weights
- resolved risk mode and policy confidence
- search wall time and sampled-state count
- opponent switch/Protect observations
- connection/reconnect and battle lifecycle events

The `/overlay` view is intentionally compact and transparent so it can be used as a small browser window or an OBS Browser Source.

## Local battle features

### Attach/resume and reconnect recovery

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

Manual resume and automatic reconnect now use the same `attach_to_battle()` reconstruction path. The latest available request/rqid is used before choosing a move. If a battle choice fails because the websocket disconnected, it is not blindly resent after reconnect; state is rebuilt first.

The current battle tag is persisted to `logs/last_battle_tag.txt`.

### Risk modes

- `safe`: favors the highest-weight move
- `balanced`: normal near-best exploration
- `aggressive`: considers a wider near-best set
- `auto`: changes behavior based on remaining Pokémon and HP position

### Suggest-only mode

Add `--suggest-only` to calculate, log, and display decisions without sending `/choose`, `/switch`, or `/team` commands.

### Search and telemetry

The local search layer adds:

- CPU-based auto parallelism with a configurable cap and awareness of `search_threads`
- dynamic search effort based on turn, HP, remaining Pokémon, timer pressure, and branching factor
- an extra search pass when leading policy choices are unusually close
- persistent MCTS process-pool reuse with recovery after `BrokenProcessPool`
- light opponent switch/Protect tendency tracking
- normalized policy weights and a shared `SearchResult` model used by telemetry and the GUI

Battle summaries can be appended as text with `--summary-path` and as JSONL with `--summary-json-path`. JSON summaries include a schema version, decision logs, search timing, result information, reconnect count, replay metadata, and opponent tendency counters.

## Local customization layout

Local-only logic is isolated under `fp/custom/`:

```text
fp/custom/
  decisions.py       # decision tags + SearchResult
  events.py          # interface/event state
  opponent_model.py  # observed opponent behavior
  telemetry.py       # summaries + decision logging
  dashboard.py       # local HTTP server

fp/gui/
  dashboard.html
  overlay.html
```

Upstream-facing files should contain small hooks rather than copies of custom business logic. This is intentional so future upstream merges stay manageable.

## Other common runs

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

```bash
make docker
```

For a specific generation:

```bash
make docker GEN=gen4
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

For each update:

```bash
git checkout main
git pull origin main
git checkout -b sync/upstream-YYYY-MM
git fetch upstream
git merge upstream/main
```

Resolve structural conflicts in favor of current upstream architecture, then reapply only the smallest hooks needed for `fp/custom/`, configuration, attach/reconnect, and policy behavior.

Validate before merging:

```bash
python -m compileall -q fp
ruff check
ruff format --check --diff
pytest tests
```

Then push the sync branch, open a PR into `main`, wait for CI, and merge it.
