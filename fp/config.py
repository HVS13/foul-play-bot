import argparse
import json
import logging
import os
import sys
import tomllib
from enum import Enum, auto
from logging.handlers import RotatingFileHandler
from typing import Optional

from fp.format_spec import FormatSpec


class CustomFormatter(logging.Formatter):
    def format(self, record):
        lvl = "{}".format(record.levelname)
        return "{} {}".format(lvl.ljust(8), record.msg)


class CustomRotatingFileHandler(RotatingFileHandler):
    def __init__(self, file_name, **kwargs):
        self.base_dir = "logs"
        if not os.path.exists(self.base_dir):
            os.mkdir(self.base_dir)
        super().__init__("{}/{}".format(self.base_dir, file_name), **kwargs)

    def do_rollover(self, new_file_name):
        new_file_name = new_file_name.replace("/", "_")
        self.baseFilename = "{}/{}".format(self.base_dir, new_file_name)
        self.doRollover()


def init_logging(level, log_to_file):
    logging.getLogger("websockets").setLevel(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.INFO)
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(level)
    stdout_handler.setFormatter(CustomFormatter())
    logger.addHandler(stdout_handler)
    FoulPlayConfig.stdout_log_handler = stdout_handler

    if log_to_file:
        file_handler = CustomRotatingFileHandler("init.log")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(CustomFormatter())
        logger.addHandler(file_handler)
        FoulPlayConfig.file_log_handler = file_handler


class SaveReplay(Enum):
    always = auto()
    never = auto()
    on_loss = auto()
    on_win = auto()


class BotModes(Enum):
    challenge_user = auto()
    accept_challenge = auto()
    search_ladder = auto()
    resume_battle = auto()


_enum_auto = auto


class RiskModes(Enum):
    auto = _enum_auto()
    safe = _enum_auto()
    balanced = _enum_auto()
    aggressive = _enum_auto()


class _FoulPlayConfig:
    websocket_uri: str
    username: str
    password: str | None
    user_id: str
    avatar: str
    bot_mode: BotModes
    pokemon_format: str = ""
    smogon_stats: str = None
    search_time_ms: int
    parallelism: int
    team_preview_search_time_ms: int | None
    team_preview_search_parallelism: int | None
    search_threads: int
    run_count: int
    team_name: str
    team_list: str = None
    user_to_challenge: str
    save_replay: SaveReplay
    battle_timer: str
    suggest_only: bool
    room_name: str
    battle_tag: str | None
    risk_mode: RiskModes
    summary_path: str | None
    summary_json_path: str | None
    reconnect_retries: int
    reconnect_backoff_seconds: float
    reconnect_max_backoff_seconds: float
    log_level: str
    log_to_file: bool
    stdout_log_handler: logging.StreamHandler
    file_log_handler: Optional[CustomRotatingFileHandler]

    @staticmethod
    def _load_config_file(config_path: Optional[str]) -> dict:
        if not config_path:
            return {}
        if not os.path.exists(config_path):
            raise ValueError("Config file not found: {}".format(config_path))

        ext = os.path.splitext(config_path)[1].lower()
        try:
            if ext == ".toml":
                with open(config_path, "rb") as handle:
                    raw_config = tomllib.load(handle)
            elif ext == ".json":
                with open(config_path, "r", encoding="utf-8") as handle:
                    raw_config = json.load(handle)
            else:
                raise ValueError("Config file must use .toml or .json")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Failed to load config file {}: {}".format(config_path, exc)) from exc

        if isinstance(raw_config, dict) and isinstance(raw_config.get("foul_play"), dict):
            raw_config = raw_config["foul_play"]
        if not isinstance(raw_config, dict):
            raise ValueError("Config file must contain an object/table at the top level")

        normalized = {}
        for key, value in raw_config.items():
            if not isinstance(key, str):
                raise ValueError("Config keys must be strings")
            normalized[key.strip().replace("-", "_")] = value
        if "smogon_stats" in normalized and "smogon_stats_format" not in normalized:
            normalized["smogon_stats_format"] = normalized.pop("smogon_stats")
        if "team" in normalized and "team_name" not in normalized:
            normalized["team_name"] = normalized.pop("team")
        return normalized

    def configure(self):
        config_parser = argparse.ArgumentParser(add_help=False)
        config_parser.add_argument("--config", default=None)
        config_args, _ = config_parser.parse_known_args()
        config_defaults = self._load_config_file(config_args.config)

        parser = argparse.ArgumentParser()
        parser.add_argument("--config", default=None, help="Path to a .toml or .json config file")
        parser.add_argument(
            "--websocket-uri",
            default=None,
            help="Pokemon Showdown websocket URI, or shorthand 'ps'/'local'",
        )
        parser.add_argument("--ps-username", default=None)
        parser.add_argument("--ps-password", default=None)
        parser.add_argument("--ps-avatar", default=None)
        parser.add_argument("--bot-mode", default=None, choices=[e.name for e in BotModes])
        parser.add_argument("--user-to-challenge", default=None)
        parser.add_argument("--pokemon-format", default=None, help="e.g. gen9randombattle")
        parser.add_argument("--smogon-stats-format", default=None)
        parser.add_argument("--search-time-ms", type=int, default=100)
        parser.add_argument("--search-parallelism", type=int, default=1)
        parser.add_argument("--team-preview-search-parallelism", type=int, default=None)
        parser.add_argument("--team-preview-search-time-ms", type=int, default=None)
        parser.add_argument("--search-threads", type=int, default=1)
        parser.add_argument(
            "--auto-parallelism",
            action=argparse.BooleanOptionalAction,
            default=False,
            help="Set search parallelism from available CPUs",
        )
        parser.add_argument("--parallelism-cap", type=int, default=8)
        parser.add_argument("--run-count", type=int, default=1)
        parser.add_argument(
            "--team-name",
            default=None,
            help="Team filename/folder relative to ./fp/teams/teams/",
        )
        parser.add_argument("--team-list", default=None)
        parser.add_argument(
            "--save-replay", default="never", choices=[e.name for e in SaveReplay]
        )
        parser.add_argument(
            "--battle-timer",
            default="on",
            choices=["on", "off", "none"],
            help="Set timer at battle start, or leave it unchanged with 'none'",
        )
        parser.add_argument(
            "--suggest-only",
            action=argparse.BooleanOptionalAction,
            default=False,
            help="Log move suggestions but do not send battle choices",
        )
        parser.add_argument("--room-name", default=None)
        parser.add_argument("--battle-tag", default=None)
        parser.add_argument("--battle-url", default=None)
        parser.add_argument(
            "--risk-mode",
            default="balanced",
            choices=[e.name for e in RiskModes],
        )
        parser.add_argument("--summary-path", default=None)
        parser.add_argument("--summary-json-path", default=None)
        parser.add_argument("--reconnect-retries", type=int, default=5)
        parser.add_argument("--reconnect-backoff-seconds", type=float, default=1.0)
        parser.add_argument("--reconnect-max-backoff-seconds", type=float, default=30.0)
        parser.add_argument("--log-level", default="DEBUG")
        parser.add_argument(
            "--log-to-file",
            action=argparse.BooleanOptionalAction,
            default=False,
        )

        allowed_defaults = {action.dest for action in parser._actions}
        parser.set_defaults(
            **{k: v for k, v in config_defaults.items() if k in allowed_defaults}
        )
        args = parser.parse_args()

        self.websocket_uri = self.get_websocket(args.websocket_uri)
        self.username = args.ps_username
        self.password = args.ps_password
        self.avatar = args.ps_avatar
        self.bot_mode = BotModes[args.bot_mode] if args.bot_mode else None
        self.pokemon_format = args.pokemon_format
        self.smogon_stats = args.smogon_stats_format
        self.search_time_ms = args.search_time_ms
        self.parallelism = args.search_parallelism
        if args.auto_parallelism:
            self.parallelism = self._auto_parallelism(args.parallelism_cap)
        self.parallelism = max(1, self.parallelism)
        self.team_preview_search_time_ms = args.team_preview_search_time_ms or self.search_time_ms
        self.team_preview_search_parallelism = (
            args.team_preview_search_parallelism or self.parallelism
        )
        self.search_threads = max(1, args.search_threads)
        self.run_count = args.run_count
        self.team_name = args.team_name or self.pokemon_format
        self.team_list = args.team_list
        self.user_to_challenge = args.user_to_challenge
        self.save_replay = SaveReplay[args.save_replay]
        self.battle_timer = args.battle_timer
        self.suggest_only = args.suggest_only
        self.room_name = args.room_name
        self.battle_tag = args.battle_tag
        if args.battle_url and not self.battle_tag:
            self.battle_tag = self._battle_tag_from_url(args.battle_url)
        if self.battle_tag and not self.battle_tag.startswith("battle-"):
            self.battle_tag = "battle-{}".format(self.battle_tag)
        if self.battle_tag:
            self.battle_tag = self.battle_tag.lower()
        self.risk_mode = RiskModes[args.risk_mode]
        self.summary_path = args.summary_path
        self.summary_json_path = args.summary_json_path
        self.reconnect_retries = max(0, args.reconnect_retries)
        self.reconnect_backoff_seconds = max(0.0, args.reconnect_backoff_seconds)
        self.reconnect_max_backoff_seconds = max(
            self.reconnect_backoff_seconds, args.reconnect_max_backoff_seconds
        )
        self.log_level = args.log_level
        self.log_to_file = args.log_to_file
        self.validate_config()

    @staticmethod
    def get_websocket(websocket_uri) -> str | None:
        if websocket_uri is None:
            return None
        normalized = websocket_uri.lower().strip()
        if normalized in ["ps", "pokemonshowdown"]:
            return "wss://sim3.psim.us/showdown/websocket"
        if normalized in ["local", "localhost"]:
            return "ws://localhost:8000/showdown/websocket"
        return websocket_uri

    @staticmethod
    def _battle_tag_from_url(battle_url: str) -> str:
        cleaned = battle_url.split("#")[0].split("?")[0].rstrip("/")
        return cleaned.split("/")[-1]

    @staticmethod
    def _auto_parallelism(parallelism_cap: int) -> int:
        cpu_count = os.cpu_count() or 1
        if cpu_count <= 1:
            return 1
        return max(1, min(cpu_count - 1, max(1, parallelism_cap)))

    @property
    def format_spec(self) -> FormatSpec:
        return FormatSpec.from_format_string(self.pokemon_format)

    def validate_config(self):
        if not self.websocket_uri:
            raise AssertionError("WEBSOCKET_URI is required")
        if not self.username:
            raise AssertionError("PS_USERNAME is required")
        if not self.bot_mode:
            raise AssertionError("BOT_MODE is required")
        if not self.pokemon_format:
            raise AssertionError("POKEMON_FORMAT is required")
        if self.bot_mode == BotModes.challenge_user:
            assert self.user_to_challenge is not None, (
                "If bot_mode is `CHALLENGE_USER`, you must declare USER_TO_CHALLENGE"
            )
        if self.bot_mode == BotModes.resume_battle:
            assert self.battle_tag is not None, (
                "If bot_mode is `RESUME_BATTLE`, you must declare BATTLE_TAG or BATTLE_URL"
            )
            self.run_count = 1


FoulPlayConfig = _FoulPlayConfig()
