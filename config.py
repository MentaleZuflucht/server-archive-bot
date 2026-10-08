import os
import yaml
import logging
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Base directory of the project
BASE_DIR = Path(__file__).resolve().parent

CONFIG_FOLDER_PATH = BASE_DIR / 'config'


class BotConfig:
    """
    Singleton class to load and provide bot configuration.

    Attributes:
        token (str): The bot token.
        folder_path (str): The path to the folder where attachments are saved.
        channel_ids (list): A list of channel IDs that should be archived.
        archiving (bool): Flag to enable archiving.
        db_path (str): Path to the SQLite database file.
    """

    _instance = None

    def __new__(cls):
        """
        Creates a new instance of BotConfig if it doesn't exist.

        Returns:
            BotConfig: The singleton instance of BotConfig.
        """
        if cls._instance is None:
            cls._instance = super(BotConfig, cls).__new__(cls)
            cls._instance._load_config()
        return cls._instance

    def _load_config(self):
        """
        Loads configuration from a YAML file and environment variables.

        Bot token is loaded from BOT_TOKEN environment variable first,
        falling back to YAML file if not found.

        Raises:
            FileNotFoundError: If the bot configuration file is not found.
            yaml.YAMLError: If there is an error parsing the YAML file.
            Exception: For any other unexpected errors.
        """
        try:
            with open(CONFIG_FOLDER_PATH / 'bot_config.yaml', 'r') as config_file:
                config = yaml.safe_load(config_file)

                # Load bot token from environment variable first, fallback to YAML
                self.token = os.getenv('BOT_TOKEN', config.get('token'))
                if not self.token:
                    raise ValueError("Bot token not found in environment variable BOT_TOKEN or config file")

                self.folder_path = config['folder_path']
                self.channel_ids = config['channel_ids']
                self.archiving = config['archiving']

        except FileNotFoundError:
            logging.error(f"Bot configuration file not found: {CONFIG_FOLDER_PATH / 'bot_config.yaml'}")
            raise
        except yaml.YAMLError as e:
            logging.error(f"Error parsing YAML file: {e}")
            raise
        except Exception as e:
            logging.error(f"Unexpected error in Bot Configuration: {e}")
            raise


def get_bot_config():
    """
    Gets the singleton instance of BotConfig.

    Returns:
        BotConfig: The singleton instance of BotConfig.
    """
    return BotConfig()
