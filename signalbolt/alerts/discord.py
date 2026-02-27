"""
Discord webhook integration for SignalBolt.

Setup:
1. Create webhook in Discord channel settings
2. Add to .env:
   DISCORD_WEBHOOK_URL=your_webhook_url

Usage:
    alert = DiscordAlert(config)
    alert.send("Hello Discord!")
    alert.send_embed(embed_dict)
"""

import os
import requests
from typing import Optional, Dict, Any

from signalbolt.core.config import Config
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.alerts.discord")


class DiscordAlert:
    """
    Discord webhook alert sender.

    Usage:
        alert = DiscordAlert(config)
        alert.send("🚀 Signal: BTCUSDT LONG!")

        # Or with embed
        alert.send_embed({
            "title": "New Signal",
            "description": "BTCUSDT LONG",
            "color": 0x00FF00
        })
    """

    def __init__(self, config: Optional[Config] = None):
        """Initialize Discord alerts."""
        self.webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "")

        if config:
            self.webhook_url = self.webhook_url or config.get(
                "discord", "webhook_url", default=""
            )

        self.enabled = bool(self.webhook_url)
        self.bot_name = "SignalBolt"
        self.avatar_url = None

        if self.enabled:
            log.info("DiscordAlert initialized")
        else:
            log.warning("DiscordAlert disabled (missing webhook_url)")

    def send(self, message: str) -> bool:
        """
        Send simple text message.

        Args:
            message: Message text

        Returns:
            True if sent successfully
        """
        if not self.enabled:
            return False

        try:
            payload = {
                "content": message,
                "username": self.bot_name,
            }

            if self.avatar_url:
                payload["avatar_url"] = self.avatar_url

            response = requests.post(self.webhook_url, json=payload, timeout=10)

            if response.status_code in (200, 204):
                log.debug("Discord message sent")
                return True
            else:
                log.error(f"Discord error: {response.status_code}")
                return False

        except Exception as e:
            log.error(f"Discord send error: {e}")
            return False

    def send_embed(self, embed: Dict[str, Any]) -> bool:
        """
        Send embed message.

        Args:
            embed: Discord embed dict

        Returns:
            True if sent successfully
        """
        if not self.enabled:
            return False

        try:
            payload = {
                "embeds": [embed],
                "username": self.bot_name,
            }

            if self.avatar_url:
                payload["avatar_url"] = self.avatar_url

            response = requests.post(self.webhook_url, json=payload, timeout=10)

            return response.status_code in (200, 204)

        except Exception as e:
            log.error(f"Discord embed error: {e}")
            return False

    def send_signal(self, signal: Any) -> bool:
        """Send signal as embed."""
        from signalbolt.signals_only.formatter import SignalFormatter

        formatter = SignalFormatter()
        embed = formatter.discord_embed(signal)

        return self.send_embed(embed)

    def test_connection(self) -> bool:
        """Test webhook connection."""
        return self.send("🤖 SignalBolt connected!")


# =============================================================================
# CONVENIENCE
# =============================================================================


def create_discord_alert(config: Optional[Config] = None) -> DiscordAlert:
    """Create Discord alert instance."""
    return DiscordAlert(config)
