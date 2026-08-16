import asyncio
import json
import logging
import time

import requests
import websockets

from fp.config import FoulPlayConfig

logger = logging.getLogger(__name__)


class LoginError(Exception):
    pass


class SaveReplayError(Exception):
    pass


class PSWebsocketClient:
    websocket = None
    address = None
    login_uri = None
    username = None
    password = None
    last_message = None
    last_challenge_time = 0
    user_id = None
    rooms = None
    reconnected = False
    reconnect_count = 0

    @classmethod
    async def create(cls, username, password, address):
        self = PSWebsocketClient()
        self.username = username
        self.password = password
        self.address = address
        self.rooms = set()
        self.reconnected = False
        self.reconnect_count = 0
        self.login_uri = (
            "https://play.pokemonshowdown.com/api/login"
            if password
            else "https://play.pokemonshowdown.com/action.php?"
        )
        await self._connect()
        return self

    async def _connect(self):
        self.websocket = await websockets.connect(self.address)

    async def join_room(self, room_name):
        message = "/join {}".format(room_name)
        await self.send_message("", [message])
        if room_name:
            self.rooms.add(room_name)
        logger.debug("Joined room '{}'".format(room_name))

    async def receive_message(self):
        while True:
            try:
                message = await self.websocket.recv()
                logger.debug("Received message from websocket: {}".format(message))
                return message
            except (websockets.ConnectionClosed, OSError) as exc:
                await self._reconnect(exc)

    @staticmethod
    def _is_battle_choice(room, message_list):
        if not room or not room.startswith("battle-") or not message_list:
            return False
        command = message_list[0].strip().lower()
        return command.startswith(("/choose", "/switch", "/team"))

    async def send_message(self, room, message_list):
        if FoulPlayConfig.suggest_only and self._is_battle_choice(room, message_list):
            logger.info("Suggest-only: not sending %s", message_list[0])
            return

        message = room + "|" + "|".join(message_list)
        logger.debug("Sending message to websocket: {}".format(message))
        while True:
            try:
                await self.websocket.send(message)
                self.last_message = message
                if room:
                    self.rooms.add(room)
                return
            except (websockets.ConnectionClosed, OSError) as exc:
                await self._reconnect(exc)

    async def avatar(self, avatar):
        await self.send_message("", ["/avatar {}".format(avatar)])
        await self.send_message("", ["/cmd userdetails {}".format(self.username)])
        while True:
            msg = await self.receive_message()
            msg_split = msg.split("|")
            if len(msg_split) > 3 and msg_split[1] == "queryresponse":
                user_details = json.loads(msg_split[3])
                if user_details["avatar"] == avatar:
                    logger.info("Avatar set to {}".format(avatar))
                else:
                    logger.warning(
                        "Could not set avatar to {}, avatar is {}".format(
                            avatar, user_details["avatar"]
                        )
                    )
                break

    async def close(self):
        if self.websocket is not None:
            await self.websocket.close()

    async def get_id_and_challstr(self):
        while True:
            message = await self.receive_message()
            split_message = message.split("|")
            if len(split_message) > 3 and split_message[1] == "challstr":
                return split_message[2], split_message[3]

    async def login(self):
        logger.info("Logging in...")
        client_id, challstr = await self.get_id_and_challstr()
        guest_login = self.password is None

        if guest_login:
            response = requests.post(
                self.login_uri,
                data={
                    "act": "getassertion",
                    "userid": self.username,
                    "challstr": "|".join([client_id, challstr]),
                },
            )
        else:
            response = requests.post(
                self.login_uri,
                data={
                    "name": self.username,
                    "pass": self.password,
                    "challstr": "|".join([client_id, challstr]),
                },
            )

        if response.status_code != 200:
            logger.error(
                "Could not get assertion\nDetails:\n{}".format(response.content)
            )
            raise LoginError("Could not get assertion")

        if guest_login:
            assertion = response.text
        else:
            response_json = json.loads(response.text[1:])
            if "actionsuccess" not in response_json:
                logger.error("Login Unsuccessful: {}".format(response_json))
                raise LoginError("Could not log-in: {}".format(response_json))
            assertion = response_json.get("assertion")

        message = ["/trn " + self.username + ",0," + assertion]
        logger.info("Successfully logged in")
        await self.send_message("", message)
        await asyncio.sleep(3)
        self.user_id = (
            self.username if guest_login else response_json["curuser"]["userid"]
        )
        return self.user_id

    async def _reconnect(self, exc):
        max_retries = FoulPlayConfig.reconnect_retries
        if max_retries <= 0:
            raise exc

        for attempt in range(1, max_retries + 1):
            wait_seconds = min(
                FoulPlayConfig.reconnect_backoff_seconds * (2 ** (attempt - 1)),
                FoulPlayConfig.reconnect_max_backoff_seconds,
            )
            logger.warning(
                "Websocket disconnected (%s). Reconnect attempt %s/%s in %ss",
                exc,
                attempt,
                max_retries,
                round(wait_seconds, 2),
            )
            await asyncio.sleep(wait_seconds)
            try:
                await self._connect()
                await self.login()
                if FoulPlayConfig.avatar is not None:
                    await self.avatar(FoulPlayConfig.avatar)
                rooms = list(self.rooms)
                self.rooms.clear()
                for room in rooms:
                    await self.join_room(room)
                self.reconnected = True
                self.reconnect_count += 1
                logger.info("Reconnected successfully")
                return
            except Exception as reconnect_exc:
                logger.warning(
                    "Reconnect attempt %s failed: %s", attempt, reconnect_exc
                )

        logger.error("Max reconnect attempts reached")
        raise exc

    def consume_reconnect_flag(self) -> bool:
        if self.reconnected:
            self.reconnected = False
            return True
        return False

    async def update_team(self, team):
        await self.send_message("", ["/utm {}".format(team)])

    async def challenge_user(self, user_to_challenge, battle_format):
        logger.info("Challenging {}...".format(user_to_challenge))
        message = ["/challenge {},{}".format(user_to_challenge, battle_format)]
        await self.send_message("", message)
        self.last_challenge_time = time.time()

    async def accept_challenge(self, battle_format, room_name):
        if room_name is not None:
            await self.join_room(room_name)

        logger.info("Waiting for a {} challenge".format(battle_format))
        username = None
        while username is None:
            msg = await self.receive_message()
            split_msg = msg.split("|")
            if (
                len(split_msg) == 9
                and split_msg[1] == "pm"
                and split_msg[3].strip().replace("!", "").replace("‽", "")
                == self.username
                and split_msg[4].startswith("/challenge")
                and split_msg[5] == battle_format
            ):
                username = split_msg[2].strip()

        await self.send_message("", ["/accept " + username])

    async def search_for_match(self, battle_format):
        logger.info("Searching for ranked {} match".format(battle_format))
        await self.send_message("", ["/search {}".format(battle_format)])

    async def leave_battle(self, battle_tag):
        await self.send_message("", ["/leave {}".format(battle_tag)])
        while True:
            msg = await self.receive_message()
            if battle_tag in msg and "deinit" in msg:
                return

    async def save_replay(self, battle_tag):
        await self.send_message(battle_tag, ["/savereplay"])
