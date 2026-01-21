import argparse
import logging
import os
import sys
from enum import Enum, auto
from logging.handlers import RotatingFileHandler
from typing import Optional


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
    websockets_logger = logging.getLogger("websockets")
    websockets_logger.setLevel(logging.INFO)
    requests_logger = logging.getLogger("urllib3")
    requests_logger.setLevel(logging.INFO)

    # Gets the root logger to set handlers/formatters
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(level)
    stdout_handler.setFormatter(CustomFormatter())
    logger.addHandler(stdout_handler)
    FoulPlayConfig.stdout_log_handler = stdout_handler

    if log_to_file:
        file_handler = CustomRotatingFileHandler("init.log")
        file_handler.setLevel(logging.DEBUG)  # file logs are always debug
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


class RiskModes(Enum):
    auto = auto()
    safe = auto()
    balanced = auto()
    aggressive = auto()


class _FoulPlayConfig:
    websocket_uri: str
    username: str
    password: str
    user_id: str
    avatar: str
    bot_mode: BotModes
    pokemon_format: str = ""
    smogon_stats: str = None
    search_time_ms: int
    parallelism: int
    run_count: int
    team_name: str
    user_to_challenge: str
    save_replay: SaveReplay
    battle_timer: str
    suggest_only: bool
    room_name: str
    battle_tag: str
    risk_mode: RiskModes
    summary_path: str
    summary_json_path: str
    reconnect_retries: int
    reconnect_backoff_seconds: float
    reconnect_max_backoff_seconds: float
    log_level: str
    log_to_file: bool
    stdout_log_handler: logging.StreamHandler
    file_log_handler: Optional[CustomRotatingFileHandler]

    def configure(self):
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--websocket-uri",
            required=True,
            help="The PokemonShowdown websocket URI, e.g. wss://sim3.psim.us/showdown/websocket",
        )
        parser.add_argument("--ps-username", required=True)
        parser.add_argument("--ps-password", required=True)
        parser.add_argument("--ps-avatar", default=None)
        parser.add_argument(
            "--bot-mode", required=True, choices=[e.name for e in BotModes]
        )
        parser.add_argument(
            "--user-to-challenge",
            default=None,
            help="If bot_mode is `challenge_user`, this is required",
        )
        parser.add_argument(
            "--pokemon-format", required=True, help="e.g. gen9randombattle"
        )
        parser.add_argument(
            "--smogon-stats-format",
            default=None,
            help="Overwrite which smogon stats are used to infer unknowns. If not set, defaults to the --pokemon-format value.",
        )
        parser.add_argument(
            "--search-time-ms",
            type=int,
            default=100,
            help="Time to search per battle in milliseconds",
        )
        parser.add_argument(
            "--search-parallelism",
            type=int,
            default=1,
            help="Number of states to search in parallel",
        )
        parser.add_argument(
            "--auto-parallelism",
            action="store_true",
            help="Automatically set search parallelism based on CPU count",
        )
        parser.add_argument(
            "--parallelism-cap",
            type=int,
            default=8,
            help="Upper bound when --auto-parallelism is enabled",
        )
        parser.add_argument(
            "--run-count",
            type=int,
            default=1,
            help="Number of PokemonShowdown battles to run",
        )
        parser.add_argument(
            "--team-name",
            default=None,
            help="Which team to use. Can be a filename or a foldername relative to ./teams/teams/. "
            "If a foldername, a random team from that folder will be chosen each battle. "
            "If not set, defaults to the --pokemon-format value.",
        )
        parser.add_argument(
            "--save-replay",
            default="never",
            choices=[e.name for e in SaveReplay],
            help="When to save replays",
        )
        parser.add_argument(
            "--battle-timer",
            default="on",
            choices=["on", "off", "none"],
            help="Whether to enable the battle timer at the start of each battle",
        )
        parser.add_argument(
            "--suggest-only",
            action="store_true",
            help="Only log suggested moves; do not send them to the server",
        )
        parser.add_argument(
            "--room-name",
            default=None,
            help="If bot_mode is `accept_challenge`, the room to join while waiting",
        )
        parser.add_argument(
            "--battle-tag",
            default=None,
            help="If bot_mode is `resume_battle`, the battle room id (e.g. battle-gen9ou-1234)",
        )
        parser.add_argument(
            "--battle-url",
            default=None,
            help="If bot_mode is `resume_battle`, a full battle URL to parse into a battle tag",
        )
        parser.add_argument(
            "--risk-mode",
            default="balanced",
            choices=[e.name for e in RiskModes],
            help="Move selection style: auto, safe, balanced, or aggressive",
        )
        parser.add_argument(
            "--summary-path",
            default=None,
            help="Write a text summary for each battle to this file (appends)",
        )
        parser.add_argument(
            "--summary-json-path",
            default=None,
            help="Write JSONL summaries for each battle to this file (appends)",
        )
        parser.add_argument(
            "--reconnect-retries",
            type=int,
            default=5,
            help="Max reconnect attempts after websocket disconnects",
        )
        parser.add_argument(
            "--reconnect-backoff-seconds",
            type=float,
            default=1.0,
            help="Initial reconnect backoff in seconds",
        )
        parser.add_argument(
            "--reconnect-max-backoff-seconds",
            type=float,
            default=30.0,
            help="Max reconnect backoff in seconds",
        )
        parser.add_argument("--log-level", default="DEBUG", help="Python logging level")
        parser.add_argument(
            "--log-to-file",
            action="store_true",
            help="When enabled, DEBUG logs will be written to a file in the logs/ directory",
        )

        args = parser.parse_args()
        self.websocket_uri = args.websocket_uri
        self.username = args.ps_username
        self.password = args.ps_password
        self.avatar = args.ps_avatar
        self.bot_mode = BotModes[args.bot_mode]
        self.pokemon_format = args.pokemon_format
        self.smogon_stats = args.smogon_stats_format
        self.search_time_ms = args.search_time_ms
        self.parallelism = args.search_parallelism
        if args.auto_parallelism:
            self.parallelism = self._auto_parallelism(args.parallelism_cap)
        self.parallelism = max(1, self.parallelism)
        self.run_count = args.run_count
        self.team_name = args.team_name or self.pokemon_format
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
        self.reconnect_retries = args.reconnect_retries
        self.reconnect_backoff_seconds = args.reconnect_backoff_seconds
        self.reconnect_max_backoff_seconds = args.reconnect_max_backoff_seconds
        self.log_level = args.log_level
        self.log_to_file = args.log_to_file

        self.validate_config()

    @staticmethod
    def _battle_tag_from_url(battle_url: str) -> str:
        cleaned = battle_url.split("#")[0].split("?")[0].rstrip("/")
        return cleaned.split("/")[-1]

    @staticmethod
    def _auto_parallelism(parallelism_cap: int) -> int:
        cpu_count = os.cpu_count() or 1
        if cpu_count <= 1:
            return 1
        return max(1, min(cpu_count - 1, parallelism_cap))

    def requires_team(self) -> bool:
        return not (
            "random" in self.pokemon_format or "battlefactory" in self.pokemon_format
        )

    def validate_config(self):
        if self.bot_mode == BotModes.challenge_user:
            assert (
                self.user_to_challenge is not None
            ), "If bot_mode is `CHALLENGE_USER`, you must declare USER_TO_CHALLENGE"
        if self.bot_mode == BotModes.resume_battle:
            assert (
                self.battle_tag is not None
            ), "If bot_mode is `RESUME_BATTLE`, you must declare BATTLE_TAG or BATTLE_URL"
            self.run_count = 1


FoulPlayConfig = _FoulPlayConfig()
