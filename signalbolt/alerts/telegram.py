"""
Telegram integration for SignalBolt.

Features:
- Send signal alerts
- Interactive bot with commands
- Status updates
- Daily summaries

Setup:
1. Create bot via @BotFather
2. Get token and chat_id
3. Add to .env:
   TELEGRAM_BOT_TOKEN=your_token
   TELEGRAM_CHAT_ID=your_chat_id

Usage:
    # Simple alerts
    alert = TelegramAlert(config)
    alert.send("Hello!")

    # Interactive bot
    bot = TelegramBot(config, engine)
    bot.start()
"""

import os
import time
import threading
import requests
from datetime import datetime
from typing import Optional, Callable, List, Dict, Any
from dataclasses import dataclass

from signalbolt.core.config import Config
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.alerts.telegram")


# =============================================================================
# TELEGRAM ALERT (Simple)
# =============================================================================


class TelegramAlert:
    """
    Simple Telegram alert sender.

    Usage:
        alert = TelegramAlert(config)
        alert.send("🚀 New signal: BTCUSDT LONG")
    """

    def __init__(self, config: Optional[Config] = None):
        """Initialize alert sender."""
        # Get credentials from env or config
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

        if config:
            self.token = self.token or config.get("telegram", "bot_token", default="")
            self.chat_id = self.chat_id or config.get("telegram", "chat_id", default="")

        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.enabled = bool(self.token and self.chat_id)

        if self.enabled:
            log.info("TelegramAlert initialized")
        else:
            log.warning("TelegramAlert disabled (missing token or chat_id)")

    def send(self, message: str, parse_mode: str = "HTML") -> bool:
        """
        Send message.

        Args:
            message: Message text
            parse_mode: 'HTML' or 'MarkdownV2'

        Returns:
            True if sent successfully
        """
        if not self.enabled:
            log.debug("Telegram disabled, skipping send")
            return False

        try:
            url = f"{self.base_url}/sendMessage"

            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }

            response = requests.post(url, json=payload, timeout=10)

            if response.status_code == 200:
                log.debug("Telegram message sent")
                return True
            else:
                log.error(f"Telegram error: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            log.error(f"Telegram send error: {e}")
            return False

    def send_photo(self, photo_url: str, caption: str = "") -> bool:
        """Send photo with caption."""
        if not self.enabled:
            return False

        try:
            url = f"{self.base_url}/sendPhoto"

            payload = {
                "chat_id": self.chat_id,
                "photo": photo_url,
                "caption": caption,
                "parse_mode": "HTML",
            }

            response = requests.post(url, json=payload, timeout=10)
            return response.status_code == 200

        except Exception as e:
            log.error(f"Telegram photo error: {e}")
            return False

    def test_connection(self) -> bool:
        """Test connection."""
        return self.send("🤖 SignalBolt connected!")


# =============================================================================
# TELEGRAM BOT (Interactive)
# =============================================================================


@dataclass
class TelegramUpdate:
    """Telegram update."""

    update_id: int
    chat_id: int
    user_id: int
    username: str
    text: str
    timestamp: datetime


class TelegramBot:
    """
    Interactive Telegram bot with commands.

    Commands:
        /start - Welcome message
        /status - Show engine status
        /signals - Recent signals
        /stats - Session statistics
        /pause - Pause scanning
        /resume - Resume scanning
        /stop - Stop engine
        /help - Show help

    Usage:
        bot = TelegramBot(config, engine)
        bot.start()  # Starts polling in background

        # Later
        bot.stop()
    """

    def __init__(
        self,
        config: Config,
        engine: Any = None,  # SignalsOnlyEngine or PaperEngine
        allowed_users: Optional[List[int]] = None,
    ):
        """
        Initialize bot.

        Args:
            config: Config instance
            engine: Engine instance for control
            allowed_users: List of allowed user IDs (None = allow all)
        """
        self.config = config
        self.engine = engine
        self.allowed_users = allowed_users

        # Credentials
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.enabled = bool(self.token)

        # State
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_update_id = 0

        # Command handlers
        self._commands: Dict[str, Callable] = {
            "/start": self._cmd_start,
            "/help": self._cmd_help,
            "/status": self._cmd_status,
            "/signals": self._cmd_signals,
            "/stats": self._cmd_stats,
            "/pause": self._cmd_pause,
            "/resume": self._cmd_resume,
            "/stop": self._cmd_stop,
            "/ping": self._cmd_ping,
        }

        if self.enabled:
            log.info("TelegramBot initialized")
        else:
            log.warning("TelegramBot disabled (missing token)")

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    def start(self):
        """Start bot polling in background thread."""
        if not self.enabled:
            log.warning("Cannot start bot: not configured")
            return

        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._polling_loop, name="TelegramBot", daemon=True
        )
        self._thread.start()

        log.info("TelegramBot started")

        # Send startup message
        self._send(f"🤖 *SignalBolt Bot Started*\n\nType /help for commands")

    def stop(self):
        """Stop bot."""
        self._running = False

        if self._thread:
            self._thread.join(timeout=5)

        log.info("TelegramBot stopped")

    # =========================================================================
    # POLLING
    # =========================================================================

    def _polling_loop(self):
        """Main polling loop."""
        log.debug("Polling loop started")

        while self._running:
            try:
                updates = self._get_updates()

                for update in updates:
                    self._handle_update(update)

                time.sleep(1)

            except Exception as e:
                log.error(f"Polling error: {e}")
                time.sleep(5)

        log.debug("Polling loop ended")

    def _get_updates(self) -> List[TelegramUpdate]:
        """Get new updates from Telegram."""
        try:
            url = f"{self.base_url}/getUpdates"

            params = {
                "offset": self._last_update_id + 1,
                "timeout": 30,
                "allowed_updates": ["message"],
            }

            response = requests.get(url, params=params, timeout=35)

            if response.status_code != 200:
                return []

            data = response.json()

            if not data.get("ok"):
                return []

            updates = []

            for update in data.get("result", []):
                self._last_update_id = update["update_id"]

                message = update.get("message", {})
                chat = message.get("chat", {})
                user = message.get("from", {})

                if message.get("text"):
                    updates.append(
                        TelegramUpdate(
                            update_id=update["update_id"],
                            chat_id=chat.get("id", 0),
                            user_id=user.get("id", 0),
                            username=user.get("username", "unknown"),
                            text=message.get("text", ""),
                            timestamp=datetime.now(),
                        )
                    )

            return updates

        except Exception as e:
            log.debug(f"Get updates error: {e}")
            return []

    def _handle_update(self, update: TelegramUpdate):
        """Handle incoming update."""
        # Check if user is allowed
        if self.allowed_users and update.user_id not in self.allowed_users:
            log.warning(f"Unauthorized user: {update.user_id} ({update.username})")
            return

        text = update.text.strip()

        # Extract command
        if text.startswith("/"):
            command = text.split()[0].lower()
            command = command.split("@")[0]  # Remove @botname

            handler = self._commands.get(command)

            if handler:
                log.debug(f"Command: {command} from {update.username}")
                handler(update)
            else:
                self._send_to(
                    update.chat_id,
                    f"❓ Unknown command: {command}\nType /help for available commands",
                )

    # =========================================================================
    # COMMANDS
    # =========================================================================

    def _cmd_start(self, update: TelegramUpdate):
        """Handle /start command."""
        msg = """
🤖 <b>SignalBolt Bot</b>

Welcome! I'll send you trading signals and let you control the bot.

<b>Commands:</b>
/status - Show current status
/signals - Recent signals
/stats - Session statistics
/pause - Pause scanning
/resume - Resume scanning
/help - Show all commands

<i>Bot is ready!</i>
"""
        self._send_to(update.chat_id, msg)

    def _cmd_help(self, update: TelegramUpdate):
        """Handle /help command."""
        msg = """
📚 <b>Available Commands</b>

<b>Info:</b>
/status - Show engine status
/signals - Last 5 signals
/stats - Session statistics
/ping - Check if bot is alive

<b>Control:</b>
/pause - Pause scanning
/resume - Resume scanning
/stop - Stop the engine

<b>Other:</b>
/help - Show this help
"""
        self._send_to(update.chat_id, msg)

    def _cmd_status(self, update: TelegramUpdate):
        """Handle /status command."""
        if not self.engine:
            self._send_to(update.chat_id, "❌ No engine connected")
            return

        try:
            status = self.engine.get_status()

            state_emoji = {
                "running": "🟢",
                "paused": "🟡",
                "stopped": "🔴",
            }.get(status.get("state", ""), "❓")

            msg = f"""
📊 <b>Engine Status</b>

{state_emoji} State: <code>{status.get("state", "unknown").upper()}</code>
📁 Session: <code>{status.get("session", "None")}</code>
⏱️ Runtime: <code>{status.get("runtime_minutes", 0):.1f} min</code>

📈 Scans: <code>{status.get("scan_count", 0)}</code>
🎯 Signals: <code>{status.get("signal_count", 0)}</code>
🔔 Alerts: <code>{status.get("alert_count", 0)}</code>
"""
            self._send_to(update.chat_id, msg)

        except Exception as e:
            self._send_to(update.chat_id, f"❌ Error: {e}")

    def _cmd_signals(self, update: TelegramUpdate):
        """Handle /signals command."""
        if not self.engine or not self.engine.session:
            self._send_to(update.chat_id, "❌ No session active")
            return

        try:
            signals = self.engine.session.get_recent_signals(hours=24)[-5:]

            if not signals:
                self._send_to(update.chat_id, "📭 No signals in last 24 hours")
                return

            msg = "🎯 <b>Recent Signals</b>\n\n"

            for s in reversed(signals):
                emoji = "📈" if s.direction == "LONG" else "📉"
                time_str = s.timestamp.strftime("%H:%M")

                msg += (
                    f"{emoji} <code>{s.symbol}</code> {s.direction} [{s.timeframe}]\n"
                )
                msg += f"   Price: ${s.price:.6f} | Score: {s.score:.0f}\n"
                msg += f"   ⏰ {time_str}\n\n"

            self._send_to(update.chat_id, msg)

        except Exception as e:
            self._send_to(update.chat_id, f"❌ Error: {e}")

    def _cmd_stats(self, update: TelegramUpdate):
        """Handle /stats command."""
        if not self.engine or not self.engine.session:
            self._send_to(update.chat_id, "❌ No session active")
            return

        try:
            stats = self.engine.session.get_stats()

            msg = f"""
📊 <b>Session Statistics</b>

📁 Session: <code>{stats.get("name", "Unknown")}</code>

📈 Total Signals: <code>{stats.get("total_signals", 0)}</code>
🟢 LONG: <code>{stats.get("long_signals", 0)}</code>
🔴 SHORT: <code>{stats.get("short_signals", 0)}</code>

📆 Today: <code>{stats.get("today_signals", 0)}</code>
🕐 Last 24h: <code>{stats.get("last_24h_signals", 0)}</code>

🔔 Alerts Sent: <code>{stats.get("alerts_sent", 0)}</code>
"""

            # Top symbols
            top = stats.get("top_symbols", [])
            if top:
                msg += "\n<b>Top Symbols:</b>\n"
                for symbol, count in top[:5]:
                    msg += f"  • {symbol}: {count}\n"

            self._send_to(update.chat_id, msg)

        except Exception as e:
            self._send_to(update.chat_id, f"❌ Error: {e}")

    def _cmd_pause(self, update: TelegramUpdate):
        """Handle /pause command."""
        if not self.engine:
            self._send_to(update.chat_id, "❌ No engine connected")
            return

        try:
            self.engine.pause()
            self._send_to(update.chat_id, "⏸️ Engine paused")
        except Exception as e:
            self._send_to(update.chat_id, f"❌ Error: {e}")

    def _cmd_resume(self, update: TelegramUpdate):
        """Handle /resume command."""
        if not self.engine:
            self._send_to(update.chat_id, "❌ No engine connected")
            return

        try:
            self.engine.resume()
            self._send_to(update.chat_id, "▶️ Engine resumed")
        except Exception as e:
            self._send_to(update.chat_id, f"❌ Error: {e}")

    def _cmd_stop(self, update: TelegramUpdate):
        """Handle /stop command."""
        if not self.engine:
            self._send_to(update.chat_id, "❌ No engine connected")
            return

        self._send_to(update.chat_id, "🛑 Stopping engine...")

        try:
            self.engine.stop()
            self._send_to(update.chat_id, "✅ Engine stopped")
        except Exception as e:
            self._send_to(update.chat_id, f"❌ Error: {e}")

    def _cmd_ping(self, update: TelegramUpdate):
        """Handle /ping command."""
        self._send_to(update.chat_id, "🏓 Pong!")

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _send(self, message: str, parse_mode: str = "HTML") -> bool:
        """Send to default chat."""
        return self._send_to(self.chat_id, message, parse_mode)

    def _send_to(self, chat_id: int, message: str, parse_mode: str = "HTML") -> bool:
        """Send to specific chat."""
        if not self.enabled:
            return False

        try:
            url = f"{self.base_url}/sendMessage"

            payload = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }

            response = requests.post(url, json=payload, timeout=10)
            return response.status_code == 200

        except Exception as e:
            log.error(f"Send error: {e}")
            return False

    def send_signal_alert(self, signal: Any):
        """Send signal alert to default chat."""
        # Import from alerts.formatter instead of signals_only
        from signalbolt.alerts.formatter import SignalFormatter

        formatter = SignalFormatter()
        msg = formatter.telegram_simple(signal)

        return self._send(msg)


# =============================================================================
# CONVENIENCE
# =============================================================================


def create_telegram_alert(config: Optional[Config] = None) -> TelegramAlert:
    """Create Telegram alert instance."""
    return TelegramAlert(config)


def test_telegram_connection() -> bool:
    """Test Telegram connection."""
    alert = TelegramAlert()
    return alert.test_connection()
