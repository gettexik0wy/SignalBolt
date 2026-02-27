"""
Alert system for SignalBolt.

Supports:
- Telegram notifications
- Discord webhooks
- Future: Email, SMS, Push notifications

Environment variables loaded from .env in project root.
"""

from dotenv import load_dotenv
import os
from pathlib import Path

# ============================================================================
# LOAD .ENV (defensive - in case main __init__ wasn't imported first)
# ============================================================================

# Path: alerts/ -> signalbolt/ -> SignalBolt/
_alerts_dir = Path(__file__).parent  # SignalBolt/signalbolt/alerts/
_package_dir = _alerts_dir.parent  # SignalBolt/signalbolt/
_project_root = _package_dir.parent  # SignalBolt/
_env_path = _project_root / ".env"

if _env_path.exists():
    load_dotenv(_env_path, override=False)  # Don't override if already loaded

# ============================================================================
# IMPORTS
# ============================================================================

from signalbolt.alerts.telegram import TelegramAlert, TelegramBot
from signalbolt.alerts.discord import DiscordAlert

__all__ = [
    "TelegramAlert",
    "TelegramBot",
    "DiscordAlert",
    "check_alerts_config",
    "print_alerts_status",
]

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def check_alerts_config() -> dict:
    """
    Check which alert systems are properly configured.

    Returns:
        dict: Status of each alert system
    """
    return {
        "telegram": {
            "enabled": bool(
                os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")
            ),
            "token_set": bool(os.getenv("TELEGRAM_BOT_TOKEN")),
            "chat_id_set": bool(os.getenv("TELEGRAM_CHAT_ID")),
        },
        "discord": {
            "enabled": bool(os.getenv("DISCORD_WEBHOOK_URL")),
            "webhook_set": bool(os.getenv("DISCORD_WEBHOOK_URL")),
        },
        "env_path": str(_env_path) if _env_path.exists() else None,
    }


def print_alerts_status() -> None:
    """Print alert configuration status to console."""
    status = check_alerts_config()

    print("\n" + "=" * 60)
    print("  ALERTS CONFIGURATION STATUS")
    print("=" * 60)

    # Environment file
    if status["env_path"]:
        print(f"\n  📁 .env: {status['env_path']}")
    else:
        print(f"\n  ⚠️  .env not found!")
        print(f"     Expected at: {_env_path}")

    # Telegram
    tg = status["telegram"]
    tg_icon = "✅" if tg["enabled"] else "❌"
    print(f"\n  {tg_icon} Telegram:")
    print(f"     TELEGRAM_BOT_TOKEN: {'✓ set' if tg['token_set'] else '✗ missing'}")
    print(f"     TELEGRAM_CHAT_ID:   {'✓ set' if tg['chat_id_set'] else '✗ missing'}")

    # Discord
    dc = status["discord"]
    dc_icon = "✅" if dc["enabled"] else "❌"
    print(f"\n  {dc_icon} Discord:")
    print(f"     DISCORD_WEBHOOK_URL: {'✓ set' if dc['webhook_set'] else '✗ missing'}")

    # Instructions
    if not tg["enabled"] and not dc["enabled"]:
        print("\n  " + "-" * 56)
        print("  To configure alerts, create .env file with:")
        print("    TELEGRAM_BOT_TOKEN=your_token")
        print("    TELEGRAM_CHAT_ID=your_chat_id")
        print("    DISCORD_WEBHOOK_URL=your_webhook_url")

    print("\n" + "=" * 60 + "\n")
