# Foul Play ![umbreon](https://play.pokemonshowdown.com/sprites/xyani/umbreon.gif)
A Pokémon battle-bot that can play battles on [Pokemon Showdown](https://pokemonshowdown.com/).

Foul Play can play single battles in all generations
though currently dynamax and z-moves are not supported.

![badge](https://github.com/pmariglia/foul-play/actions/workflows/ci.yml/badge.svg)

## Python version
Requires Python 3.11+.

## Getting Started

### Configuration

Command-line arguments are used to configure Foul Play

use `python run.py --help` to see all options.

#### All CLI options (current)

Required (unless provided by `--config`):
- `--websocket-uri` (string)
- `--ps-username` (string)
- `--ps-password` (string)
- `--bot-mode` (`challenge_user|accept_challenge|search_ladder|resume_battle`)
- `--pokemon-format` (string, e.g. `gen9randombattle`)

Optional:
- `--config` (path to `.toml` or `.json`; default: `None`)
- `--ps-avatar` (string; default: `None`)
- `--user-to-challenge` (string; required when `--bot-mode challenge_user`)
- `--smogon-stats-format` (string; default: `None`)
- `--search-time-ms` (int; default: `100`)
- `--search-parallelism` (int; default: `1`)
- `--auto-parallelism` / `--no-auto-parallelism` (bool; default: `false`)
- `--parallelism-cap` (int; default: `8`)
- `--run-count` (int; default: `1`)
- `--team-name` (string; default: `--pokemon-format`)
- `--save-replay` (`always|never|on_loss|on_win`; default: `never`)
- `--battle-timer` (`on|off|none`; default: `on`)
- `--suggest-only` / `--no-suggest-only` (bool; default: `false`)
- `--room-name` (string; used by `accept_challenge`)
- `--battle-tag` (string; used by `resume_battle`)
- `--battle-url` (string; used by `resume_battle`)
- `--risk-mode` (`auto|safe|balanced|aggressive`; default: `balanced`)
- `--summary-path` (string; default: `None`)
- `--summary-json-path` (string; default: `None`)
- `--reconnect-retries` (int; default: `5`)
- `--reconnect-backoff-seconds` (float; default: `1.0`)
- `--reconnect-max-backoff-seconds` (float; default: `30.0`)
- `--log-level` (string; default: `DEBUG`)
- `--log-to-file` / `--no-log-to-file` (bool; default: `false`)

#### Config file (TOML or JSON)

You can pass a config file with `--config`, and still override any setting via CLI.

Example `config.toml`:
```toml
websocket_uri = "wss://sim3.psim.us/showdown/websocket"
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

Example usage:
```bash
python run.py --config config.toml --risk-mode aggressive
```

#### Bot modes

- `search_ladder`: queue for a ranked match
- `challenge_user`: challenge a specific user (requires `--user-to-challenge`)
- `accept_challenge`: wait for challenges (optionally `--room-name`)
- `resume_battle`: take over an in-progress battle (requires `--battle-tag` or `--battle-url`)

Note: `resume_battle` logs in as the account that is already in the battle, which will disconnect any other active session for that account.

#### Risk modes

Control how adventurous the bot is when picking among top moves:

- `auto`: adjust risk based on the current battle state
- `safe`: pick the most reliable move (lowest variance)
- `balanced`: default tradeoff of safety and exploration
- `aggressive`: consider a wider set of moves to chase higher upside

Set with `--risk-mode auto|safe|balanced|aggressive` (default: `balanced`).
`auto` leans safe when ahead on remaining Pokemon/HP and aggressive when behind.

#### Search and QoL options

- `--auto-parallelism` and `--parallelism-cap` to scale search by CPU.
- Dynamic search time increases in late-game/low-HP situations by default, and now also adapts to branching factor; low-confidence policies trigger a small extra search when time allows.
- `--summary-path` writes a text summary per battle (appends).
- `--summary-json-path` writes JSONL summaries per battle (appends), including decision logs, search timing, win reason, and replay URL (when saved).
- `--reconnect-retries`, `--reconnect-backoff-seconds`, `--reconnect-max-backoff-seconds` control websocket reconnect behavior.
- Auto-resume: on websocket reconnect during a battle, the bot rebuilds state and continues. The current battle tag is persisted to `logs/last_battle_tag.txt`.
- `--suggest-only` prints top move options with short tags (e.g. `ko`, `setup`, `pivot`).
- Opponent tendency tracking: the bot tracks opponent switch/protect rates during a battle and slightly biases move selection; stats are included in JSON summaries.

#### Defaults for new options

- `--config`: `None` (disabled)
- `--risk-mode`: `balanced`
- `--auto-parallelism`: `false`
- `--parallelism-cap`: `8`
- `--summary-path`: `None` (disabled)
- `--summary-json-path`: `None` (disabled)
- `--reconnect-retries`: `5`
- `--reconnect-backoff-seconds`: `1.0`
- `--reconnect-max-backoff-seconds`: `30.0`

### Running Locally

**1. Clone**

Clone the repository with `git clone https://github.com/pmariglia/foul-play.git`

**2. Install Requirements**

Install the requirements with `pip install -r requirements.txt`.

Note: Requires Rust to be installed on your machine to build the engine.

**4. Run**

Run with `python run.py`

Here is a minimal example that plays a gen9randombattle on Pokemon Showdown:
```bash
python run.py \
--websocket-uri wss://sim3.psim.us/showdown/websocket \
--ps-username 'My Username' \
--ps-password sekret \
--bot-mode search_ladder \
--pokemon-format gen9randombattle
```

More examples:

Accept challenges in a room:
```bash
python run.py \
--websocket-uri wss://sim3.psim.us/showdown/websocket \
--ps-username 'My Username' \
--ps-password sekret \
--bot-mode accept_challenge \
--pokemon-format gen9randombattle \
--room-name lobby
```

Challenge a specific user:
```bash
python run.py \
--websocket-uri wss://sim3.psim.us/showdown/websocket \
--ps-username 'My Username' \
--ps-password sekret \
--bot-mode challenge_user \
--user-to-challenge 'Opponent Name' \
--pokemon-format gen9randombattle
```

Resume an ongoing battle by tag or URL:
```bash
python run.py \
--websocket-uri wss://sim3.psim.us/showdown/websocket \
--ps-username 'My Username' \
--ps-password sekret \
--bot-mode resume_battle \
--pokemon-format gen9ou \
--battle-tag battle-gen9ou-123456
```

```bash
python run.py \
--websocket-uri wss://sim3.psim.us/showdown/websocket \
--ps-username 'My Username' \
--ps-password sekret \
--bot-mode resume_battle \
--pokemon-format gen9ou \
--battle-url https://play.pokemonshowdown.com/battle-gen9ou-123456
```

Add `--suggest-only` to log suggested moves without sending them.

Realistic example with new options enabled:
```bash
python run.py \
--websocket-uri wss://sim3.psim.us/showdown/websocket \
--ps-username 'My Username' \
--ps-password sekret \
--bot-mode search_ladder \
--pokemon-format gen9ou \
--risk-mode auto \
--auto-parallelism \
--parallelism-cap 6 \
--summary-path logs/battle_summary.txt \
--summary-json-path logs/battle_summary.jsonl \
--reconnect-retries 6 \
--reconnect-backoff-seconds 1.5 \
--reconnect-max-backoff-seconds 20
```

### Running with Docker

**1. Clone the repository**

`git clone https://github.com/pmariglia/foul-play.git`

**2. Build the Docker image**

Use the `Makefile` to build a Docker image
```shell
make docker
```

or for a specific generation:
```shell
make docker GEN=gen4
```

**3. Run the Docker Image**
```bash
docker run --rm --network host foul-play:latest \
--websocket-uri wss://sim3.psim.us/showdown/websocket \
--ps-username 'My Username' \
--ps-password sekret \
--bot-mode search_ladder \
--pokemon-format gen9randombattle
```

## Engine

This project uses [poke-engine](https://github.com/pmariglia/poke-engine) to search through battles.
See [the engine docs](https://poke-engine.readthedocs.io/en/latest/) for more information.

The engine must be built from source if installing locally so you must have rust installed on your machine.

### Re-Installing the Engine

It is common to want to re-install the engine for different generations of Pokémon.

`pip` will used cached .whl artifacts when installing packages
and cannot detect the `--config-settings` flag that was used to build the engine.

The following command will ensure that the engine is re-installed properly:
```shell
pip uninstall -y poke-engine && pip install -v --force-reinstall --no-cache-dir poke-engine --config-settings="build-args=--features poke-engine/<GENERATION> --no-default-features"
```

Or using the Makefile:
```shell
make poke_engine GEN=<generation>
```

For example, to re-install the engine for generation 4:
```shell
make poke_engine GEN=gen4
```

## Updating from the original repo

If you cloned this project and pushed it to your own GitHub repo, you can keep the original author as an `upstream` remote and pull updates.

Add the original repo as `upstream` once:
```bash
git remote add upstream https://github.com/pmariglia/foul-play.git
git fetch upstream
```

When you want to update your repo:
```bash
git fetch upstream
git merge upstream/main
git push origin main
```

If the original repo uses `master` instead of `main`, replace `upstream/main` with `upstream/master`.
