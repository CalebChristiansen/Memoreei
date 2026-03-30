from memoreei.connectors.base import BaseConnector, SyncResult

# Registry of available connectors (name -> class)
# Import lazily to avoid import errors when optional deps are missing
def get_connector_registry() -> dict[str, type[BaseConnector]]:
    registry = {}
    try:
        from memoreei.connectors.discord_connector import DiscordConnector
        registry["discord"] = DiscordConnector
    except ImportError:
        pass
    try:
        from memoreei.connectors.telegram_connector import TelegramConnector
        registry["telegram"] = TelegramConnector
    except ImportError:
        pass
    # Add others as they're refactored
    return registry
