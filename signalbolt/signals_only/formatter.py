"""
Signal message formatter.

Formats signals for different outputs:
- Console (colored)
- Telegram (markdown)
- Discord (embed)
- Plain text
"""

from datetime import datetime
from typing import Optional

from signalbolt.core.strategy import Signal
from signalbolt.signals_only.session import StoredSignal


class SignalFormatter:
    """
    Format signals for different outputs.

    Usage:
        formatter = SignalFormatter()

        # Console output
        print(formatter.console(signal))

        # Telegram message
        msg = formatter.telegram(signal)

        # Discord embed
        embed = formatter.discord_embed(signal)
    """

    def __init__(self, bot_name: str = "SignalBolt"):
        """Initialize formatter."""
        self.bot_name = bot_name

    # =========================================================================
    # CONSOLE
    # =========================================================================

    def console(self, signal: Signal, colored: bool = True) -> str:
        """
        Format for console output.

        Args:
            signal: Signal to format
            colored: Use ANSI colors

        Returns:
            Formatted string
        """
        if colored:
            # ANSI colors
            GREEN = "\033[92m"
            RED = "\033[91m"
            YELLOW = "\033[93m"
            CYAN = "\033[96m"
            BOLD = "\033[1m"
            RESET = "\033[0m"
        else:
            GREEN = RED = YELLOW = CYAN = BOLD = RESET = ""

        direction_color = GREEN if signal.direction == "LONG" else RED
        direction_emoji = "📈" if signal.direction == "LONG" else "📉"

        # Quality tier
        if signal.score >= 85:
            tier = f"{GREEN}PREMIUM{RESET}"
            tier_emoji = "🌟"
        elif signal.score >= 75:
            tier = f"{GREEN}HIGH{RESET}"
            tier_emoji = "⭐"
        elif signal.score >= 65:
            tier = f"{YELLOW}GOOD{RESET}"
            tier_emoji = "✅"
        else:
            tier = f"{YELLOW}FAIR{RESET}"
            tier_emoji = "📊"

        lines = [
            f"",
            f"{'═' * 50}",
            f"{direction_emoji} {BOLD}NEW SIGNAL{RESET} {tier_emoji} {tier}",
            f"{'═' * 50}",
            f"",
            f"  {CYAN}Symbol:{RESET}     {BOLD}{signal.symbol}{RESET}",
            f"  {CYAN}Direction:{RESET}  {direction_color}{signal.direction}{RESET}",
            f"  {CYAN}Price:{RESET}      ${signal.price:.8f}",
            f"  {CYAN}Score:{RESET}      {signal.score:.1f}/100",
            f"  {CYAN}Confidence:{RESET} {signal.confidence.upper()}",
            f"  {CYAN}Regime:{RESET}     {signal.regime.upper()}",
            f"",
            f"  {CYAN}Time:{RESET}       {signal.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
        ]

        if signal.notes:
            lines.append(f"  {CYAN}Notes:{RESET}      {signal.notes}")

        lines.append(f"{'═' * 50}")
        lines.append("")

        return "\n".join(lines)

    def console_compact(self, signal: Signal) -> str:
        """Compact one-line format for console."""
        emoji = "📈" if signal.direction == "LONG" else "📉"
        time_str = signal.timestamp.strftime("%H:%M:%S")

        return (
            f"{emoji} [{time_str}] {signal.symbol} {signal.direction} "
            f"@ ${signal.price:.6f} (score: {signal.score:.0f})"
        )

    # =========================================================================
    # TELEGRAM
    # =========================================================================

    def telegram(self, signal: Signal) -> str:
        """
        Format for Telegram (MarkdownV2).

        Args:
            signal: Signal to format

        Returns:
            Telegram-formatted message
        """
        direction_emoji = "📈" if signal.direction == "LONG" else "📉"

        # Quality tier
        if signal.score >= 85:
            tier_emoji = "🌟"
            tier_text = "PREMIUM"
        elif signal.score >= 75:
            tier_emoji = "⭐"
            tier_text = "HIGH"
        elif signal.score >= 65:
            tier_emoji = "✅"
            tier_text = "GOOD"
        else:
            tier_emoji = "📊"
            tier_text = "FAIR"

        # Escape special characters for MarkdownV2
        def escape(text: str) -> str:
            special_chars = [
                "_",
                "*",
                "[",
                "]",
                "(",
                ")",
                "~",
                "`",
                ">",
                "#",
                "+",
                "-",
                "=",
                "|",
                "{",
                "}",
                ".",
                "!",
            ]
            for char in special_chars:
                text = text.replace(char, f"\\{char}")
            return text

        price_str = escape(f"${signal.price:.8f}")
        time_str = signal.timestamp.strftime("%H:%M:%S")

        msg = f"""
{direction_emoji} *NEW SIGNAL* {tier_emoji} {tier_text}

*Symbol:* `{signal.symbol}`
*Direction:* {signal.direction}
*Price:* {price_str}
*Score:* {signal.score:.0f}/100
*Confidence:* {signal.confidence.upper()}
*Regime:* {signal.regime.upper()}

⏰ {escape(time_str)}
"""

        if signal.notes:
            msg += f"\n📝 _{escape(signal.notes)}_"

        msg += f"\n\n🤖 _{self.bot_name}_"

        return msg.strip()

    def telegram_simple(self, signal: Signal) -> str:
        """Simple Telegram format (HTML)."""
        direction_emoji = "📈" if signal.direction == "LONG" else "📉"

        return f"""
{direction_emoji} <b>SIGNAL: {signal.symbol}</b>

Direction: <code>{signal.direction}</code>
Price: <code>${signal.price:.8f}</code>
Score: <code>{signal.score:.0f}/100</code>
Regime: {signal.regime}

⏰ {signal.timestamp.strftime("%H:%M:%S")}
"""

    # =========================================================================
    # DISCORD
    # =========================================================================

    def discord_embed(self, signal: Signal) -> dict:
        """
        Format for Discord embed.

        Args:
            signal: Signal to format

        Returns:
            Discord embed dict
        """
        # Color based on direction
        color = 0x00FF00 if signal.direction == "LONG" else 0xFF0000

        # Quality tier
        if signal.score >= 85:
            tier = "🌟 PREMIUM"
        elif signal.score >= 75:
            tier = "⭐ HIGH"
        elif signal.score >= 65:
            tier = "✅ GOOD"
        else:
            tier = "📊 FAIR"

        direction_emoji = "📈" if signal.direction == "LONG" else "📉"

        embed = {
            "title": f"{direction_emoji} New Signal: {signal.symbol}",
            "color": color,
            "fields": [
                {"name": "Direction", "value": signal.direction, "inline": True},
                {"name": "Price", "value": f"${signal.price:.8f}", "inline": True},
                {"name": "Score", "value": f"{signal.score:.0f}/100", "inline": True},
                {"name": "Quality", "value": tier, "inline": True},
                {
                    "name": "Confidence",
                    "value": signal.confidence.upper(),
                    "inline": True,
                },
                {"name": "Regime", "value": signal.regime.upper(), "inline": True},
            ],
            "footer": {
                "text": f"{self.bot_name} • {signal.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"
            },
            "timestamp": signal.timestamp.isoformat(),
        }

        if signal.notes:
            embed["description"] = f"📝 {signal.notes}"

        return embed

    def discord_webhook_payload(self, signal: Signal) -> dict:
        """Full Discord webhook payload."""
        return {"embeds": [self.discord_embed(signal)]}

    # =========================================================================
    # PLAIN TEXT
    # =========================================================================

    def plain(self, signal: Signal) -> str:
        """Plain text format."""
        return f"""
NEW SIGNAL
==========
Symbol:     {signal.symbol}
Direction:  {signal.direction}
Price:      ${signal.price:.8f}
Score:      {signal.score:.0f}/100
Confidence: {signal.confidence}
Regime:     {signal.regime}
Time:       {signal.timestamp.strftime("%Y-%m-%d %H:%M:%S")}
"""

    # =========================================================================
    # SUMMARY
    # =========================================================================

    def daily_summary_telegram(
        self,
        total_signals: int,
        long_count: int,
        short_count: int,
        top_symbols: list,
        date: Optional[datetime] = None,
    ) -> str:
        """Format daily summary for Telegram."""
        date = date or datetime.now()
        date_str = date.strftime("%Y-%m-%d")

        symbols_text = ""
        for symbol, count in top_symbols[:5]:
            symbols_text += f"\n  • {symbol}: {count}"

        return f"""
📊 *DAILY SUMMARY* \\- {date_str}

*Total Signals:* {total_signals}
📈 LONG: {long_count}
📉 SHORT: {short_count}

*Top Symbols:*{symbols_text}

🤖 _{self.bot_name}_
"""

    def daily_summary_discord(
        self,
        total_signals: int,
        long_count: int,
        short_count: int,
        top_symbols: list,
        date: Optional[datetime] = None,
    ) -> dict:
        """Format daily summary for Discord."""
        date = date or datetime.now()

        symbols_text = "\n".join([f"{s}: {c}" for s, c in top_symbols[:5]])

        return {
            "title": f"📊 Daily Summary - {date.strftime('%Y-%m-%d')}",
            "color": 0x3498DB,
            "fields": [
                {"name": "Total Signals", "value": str(total_signals), "inline": True},
                {"name": "📈 LONG", "value": str(long_count), "inline": True},
                {"name": "📉 SHORT", "value": str(short_count), "inline": True},
                {
                    "name": "Top Symbols",
                    "value": symbols_text or "None",
                    "inline": False,
                },
            ],
            "footer": {"text": self.bot_name},
            "timestamp": date.isoformat(),
        }
