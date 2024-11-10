import logging
from collections import defaultdict

from openai import OpenAI
from sentient_campaign.agents.v1.api import IReactiveAgent
from sentient_campaign.agents.v1.message import (
    ActivityMessage,
    ActivityResponse,
    MessageChannelType,
)

from .villager import Villager
from .wolf import Wolf
from .utils import extract_players, find_my_role, get_message_type
import random

GAME_CHANNEL = "play-arena"
WOLFS_CHANNEL = "wolf's-den"
MODERATOR_NAME = "moderator"
MODEL_NAME = "Llama31-70B-Instruct"

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

logger = logging.getLogger("demo_agent")
level = logging.DEBUG
logger.setLevel(level)
logger.propagate = True
handler = logging.StreamHandler()
handler.setLevel(level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

class HackingAgent(IReactiveAgent):
    def __init__(self):
        logger.debug("WerewolfAgent initialized.")
        
    def __initialize__(self, name: str, description: str, config: dict = None):
        super().__initialize__(name, description, config)
        self._name = name
        self._description = description
        self.MODERATOR_NAME = MODERATOR_NAME
        self.WOLFS_CHANNEL = WOLFS_CHANNEL
        self.GAME_CHANNEL = GAME_CHANNEL
        self.config = config

        self.role = None
        self.villager = None
        self.wolf = None


        self.direct_messages = defaultdict(list)
        self.group_channel_messages = defaultdict(list)
        self.game_history = []  # To store the interwoven game history
        self.other_players = []

        self.llm_config = self.sentient_llm_config["config_list"][0]
        self.openai_client = OpenAI(
            api_key=self.llm_config["api_key"],
            base_url=self.llm_config["llm_base_url"],
        )

        self.model = self.llm_config["llm_model_name"]
        logger.info(
            f"WerewolfAgent initialized with name: {name}, description: {description}, and config: {config}"
        )
        self.game_intro = None

    # Called when a message is sent to the agent by the moderator. No response is expected.
    async def async_notify(self, message: ActivityMessage):
        logger.info(f"ASYNC NOTIFY called with message: {message}")
        if message.header.channel_type == MessageChannelType.DIRECT:
            user_messages = self.direct_messages.get(message.header.sender, [])
            user_messages.append(message.content.text)
            self.direct_messages[message.header.sender] = user_messages
            self.game_history.append(f"[From - {message.header.sender}| To - {self._name} (me)| Direct Message]: {message.content.text}")
            # FIRST MESSAGE FROM MODERATOR: FIND OUT ROLE
            if not len(user_messages) > 1 and message.header.sender == self.MODERATOR_NAME:
                self.role = find_my_role(message, self.model, self.openai_client, self._name)
                logger.info(f"Role found for user {self._name}: {self.role}")

                if self.role != "wolf":
                    self.villager = Villager(self._name, self.game_history, self.llm_config, self.openai_client, self.MODERATOR_NAME, self.WOLFS_CHANNEL, self.GAME_CHANNEL, self.config)
                else:
                    self.wolf = Wolf(self._name, self.other_players, self.llm_config, self.openai_client, self.MODERATOR_NAME, self.WOLFS_CHANNEL, self.GAME_CHANNEL, self.config)
            # SEER CHECK RESULT
            elif self.role == "seer":
                self.villager.receive_seer_check(message.content.text)
        else:
            group_messages = self.group_channel_messages.get(message.header.channel, [])
            group_messages.append((message.header.sender, message.content.text))
            self.group_channel_messages[message.header.channel] = group_messages
            self.game_history.append(f"[From - {message.header.sender}| To - Everyone| Group Message in {message.header.channel}]: {message.content.text}")
            # MODERATOR RULES
            if message.header.channel == self.GAME_CHANNEL and message.header.sender == self.MODERATOR_NAME and not self.game_intro:
                self.game_intro = message.content.text
                logger.info(f"target_receivers: {message.header.target_receivers}")
                all_players = extract_players(self.game_intro, self.model, self.openai_client, self._name)
                self.other_players = [name for name in all_players if name != self._name]
                logger.info(f"other_players: {self.other_players}")
                if self.wolf is not None:
                    logger.info(f"setting other_players: {self.other_players}")
                    self.wolf.alive_players = self.other_players
            # SOME OTHER EVENT: wakeup, someone died, group message in global chat, wolfs den
            else:
                if self.villager is not None:
                    self.villager.receive_global_player_message(message.content.text, message.header.sender)
                else:
                    if message.header.channel == self.WOLFS_CHANNEL and message.header.sender == self.MODERATOR_NAME:
                        self.wolf.receive_wolfs_den_moderator_message(message.content.text)
                    elif message.header.channel == self.WOLFS_CHANNEL:
                        self.wolf.receive_wolfs_message(message.content.text)
                    else:
                        self.wolf.receive_global_player_message(message.content.text, message.header.sender)
        logger.info(f"message stored in messages {message}")

    # Called by the moderator when a response is expected, e.g. seer and doctor actions, or messages to the game or wolf channels
    async def async_respond(self, message: ActivityMessage):
        logger.info(f"ASYNC RESPOND called with message: {message}")

        message_type = get_message_type(message, self.role)
        if message_type == "seer":
            response_message = self.villager.get_seer_check_target(message.content.text)
            self.game_history.append(f"[From - {message.header.sender}| To - {self._name} (me)| Direct Message]: {message.content.text}")
            self.game_history.append(f"[From - {self._name} (me)| To - {message.header.sender}| Direct Message]: {response_message}")
        elif message_type == "doctor":
            response_message = self.villager.get_doctor_save_target(message.content.text)
            self.game_history.append(f"[From - {message.header.sender}| To - {self._name} (me)| Direct Message]: {message.content.text}")
            self.game_history.append(f"[From - {self._name} (me)| To - {message.header.sender}| Direct Message]: {response_message}")
        elif message_type == "game":
            if self.villager is not None:
                response_message = self.villager.get_global_response(message.content.text, message.header.target_receivers)  
            else:
                response_message = self.wolf.get_global_response(message.content.text, message.header.target_receivers)
        elif message_type == "wolf":
            response_message = self.wolf.get_wolf_kill_target(message.content.text)
            self.game_history.append(f"[From - {message.header.sender}| To - {self._name} (me)| Group Message in {message.header.channel}]: {message.content.text}")
            self.game_history.append(f"[From - {self._name} (me)| To - {message.header.sender}| Group Message in {message.header.channel}]: {response_message}")
        
        return ActivityResponse(response=response_message)
    