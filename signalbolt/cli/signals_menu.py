"""
Signals-only mode CLI menu.

Interactive menu for:
- Starting/stopping signal monitoring
- Viewing signal history
- Managing alerts
- Session management
"""

import sys
import time
from typing import Optional, List
from datetime import datetime

from signalbolt.signals_only.engine import SignalsOnlyEngine, SignalsEngineState
from signalbolt.signals_only.session import SignalsSession
from signalbolt.signals_only.history import SignalHistory
from signalbolt.signals_only.formatter import SignalFormatter
from signalbolt.core.config import Config, list_configs
from signalbolt.cli.utils import (
    print_header,
    print_divider,
    print_table,
    input_yes_no,
    input_string,
    input_number,
    green,
    red,
    yellow,
    cyan,
    bold,
    dim,
    clear_screen,
)
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.cli.signals_menu")


class SignalsMenu:
    """
    Signals-only mode interactive menu.

    Usage:
        menu = SignalsMenu()
        menu.run()
    """

    def __init__(self):
        """Initialize menu."""
        self.engine: Optional[SignalsOnlyEngine] = None
        self.running = True
        self.selected_config = "config_safe.yaml"

    def run(self):
        """Run menu loop."""
        while self.running:
            try:
                self._show_main_menu()
            except KeyboardInterrupt:
                print(f"\n\n{yellow('Interrupted. Use Exit to quit properly.')}")
                time.sleep(1)
            except Exception as e:
                print(f"\n{red(f'Error: {e}')}")
                import traceback

                traceback.print_exc()
                input("\nPress Enter to continue...")

    # =========================================================================
    # MAIN MENU
    # =========================================================================

    def _show_main_menu(self):
        """Show main menu."""
        clear_screen()
        self._print_header()

        # Menu based on engine state
        if self.engine and self.engine.state == SignalsEngineState.RUNNING:
            self._show_running_menu()
        elif self.engine and self.engine.state == SignalsEngineState.PAUSED:
            self._show_paused_menu()
        else:
            self._show_stopped_menu()

    def _print_header(self):
        """Print status header."""
        print(f"\n{'═' * 60}")

        if self.engine and self.engine.state != SignalsEngineState.STOPPED:
            status = self.engine.get_status()
            state_emoji = "🟢" if status["state"] == "running" else "🟡"

            print(
                f"  {state_emoji} {bold('SIGNALS ONLY MODE')} │ "
                f"Session: {cyan(status['session'] or 'None')} │ "
                f"State: {status['state'].upper()}"
            )
            print(
                f"  📊 Scans: {status['scan_count']} │ "
                f"Signals: {status['signal_count']} │ "
                f"Alerts: {status['alert_count']}"
            )
        else:
            print(f"  🔴 {bold('SIGNALS ONLY MODE')} │ {dim('Not running')}")

        print(f"{'═' * 60}\n")

    def _show_stopped_menu(self):
        """Show menu when stopped."""
        print(f"  {cyan('[1]')} 🚀 Start Monitoring")
        print(f"  {cyan('[2]')} 📁 View Signal History")
        print(f"  {cyan('[3]')} 📊 Session Statistics")
        print(f"  {cyan('[4]')} ⚙️  Select Config")
        print(f"  {cyan('[5]')} 🔔 Test Alerts")
        print(f"  {cyan('[6]')} 📋 Manage Sessions")
        print()
        print(f"  {dim('[0]')} 🚪 Exit to Main Menu")
        print()

        choice = input(f"  {bold('Select option')}: ").strip()

        handlers = {
            "1": self._start_monitoring,
            "2": self._view_history,
            "3": self._view_stats,
            "4": self._select_config,
            "5": self._test_alerts,
            "6": self._manage_sessions,
            "0": self._exit_menu,
        }

        handler = handlers.get(choice)
        if handler:
            handler()

    def _show_running_menu(self):
        """Show menu when running."""
        print(f"  {cyan('[1]')} 📊 Show Status")
        print(f"  {cyan('[2]')} 🎯 View Recent Signals")
        print(f"  {cyan('[3]')} 📈 View Statistics")
        print(f"  {cyan('[4]')} ⏸️  Pause Monitoring")
        print(f"  {cyan('[5]')} 🛑 Stop Monitoring")
        print()
        print(f"  {dim('[0]')} 🚪 Exit to Main Menu")
        print()

        choice = input(f"  {bold('Select option')}: ").strip()

        handlers = {
            "1": self._show_status,
            "2": self._view_recent_signals,
            "3": self._view_stats,
            "4": self._pause_engine,
            "5": self._stop_engine,
            "0": self._exit_menu,
        }

        handler = handlers.get(choice)
        if handler:
            handler()

    def _show_paused_menu(self):
        """Show menu when paused."""
        print(f"  {cyan('[1]')} ▶️  Resume Monitoring")
        print(f"  {cyan('[2]')} 📊 Show Status")
        print(f"  {cyan('[3]')} 🎯 View Recent Signals")
        print(f"  {cyan('[4]')} 🛑 Stop Monitoring")
        print()
        print(f"  {dim('[0]')} 🚪 Exit to Main Menu")
        print()

        choice = input(f"  {bold('Select option')}: ").strip()

        handlers = {
            "1": self._resume_engine,
            "2": self._show_status,
            "3": self._view_recent_signals,
            "4": self._stop_engine,
            "0": self._exit_menu,
        }

        handler = handlers.get(choice)
        if handler:
            handler()

    # =========================================================================
    # START MONITORING
    # =========================================================================

    def _start_monitoring(self):
        """Start signal monitoring."""
        clear_screen()
        print_header("START SIGNAL MONITORING")

        # Select config
        configs = list_configs("signals")
        if not configs:
            configs = list_configs("paper")
        if not configs:
            configs = [
                "config_safe.yaml",
                "config_balanced.yaml",
                "config_aggressive.yaml",
            ]

        print("\n📋 Available configs:")
        for i, cfg in enumerate(configs, 1):
            marker = " ← current" if cfg == self.selected_config else ""
            print(f"  {cyan(str(i))}. {cfg}{dim(marker)}")

        config_choice = input(f"\nSelect config (default: 1): ").strip()

        if config_choice:
            try:
                idx = int(config_choice) - 1
                if 0 <= idx < len(configs):
                    self.selected_config = configs[idx]
            except ValueError:
                pass

        # Session name
        sessions = SignalsSession.list_sessions()

        print("\n📁 Available sessions:")
        if sessions:
            for i, s in enumerate(sessions, 1):
                print(f"  {cyan(str(i))}. {s['name']} ({s['total_signals']} signals)")
            print(f"  {cyan(str(len(sessions) + 1))}. {green('+ Create new')}")

            choice = input(f"\nSelect session (default: new): ").strip()

            try:
                idx = int(choice) - 1 if choice else len(sessions)

                if idx == len(sessions):
                    session_name = input_string("  Session name", min_length=1)
                    create_new = True
                elif 0 <= idx < len(sessions):
                    session_name = sessions[idx]["session_id"]
                    create_new = False
                else:
                    session_name = input_string("  Session name", min_length=1)
                    create_new = True
            except ValueError:
                session_name = input_string("  Session name", min_length=1)
                create_new = True
        else:
            print(f"  {dim('No sessions found')}")
            session_name = input_string("  Session name", min_length=1)
            create_new = True

        # Confirm
        print(f"\n{'─' * 40}")
        print(f"  Config:  {yellow(self.selected_config)}")
        print(f"  Session: {cyan(session_name)} {green('(new)') if create_new else ''}")
        print(f"{'─' * 40}")

        if not input_yes_no("\nStart monitoring?", default=True):
            return

        # Start engine
        print(f"\n🚀 Starting signal monitoring...")

        self.engine = SignalsOnlyEngine(self.selected_config)

        success = self.engine.start_session(
            session_name=session_name, create_new=create_new
        )

        if not success:
            print(red("\n❌ Failed to start session"))
            input("\nPress Enter to continue...")
            return

        print(green("\n✅ Session started!"))
        print(f"\n{yellow('Starting monitoring loop...')}")
        print(dim("Press Ctrl+C to return to menu\n"))

        time.sleep(1)

        # Run engine
        try:
            self.engine.run()
        except KeyboardInterrupt:
            print(f"\n\n{yellow('Stopping engine...')}")
            self.engine.stop()

        input("\nPress Enter to continue...")

    # =========================================================================
    # VIEW HISTORY
    # =========================================================================

    def _view_history(self):
        """View signal history."""
        clear_screen()
        print_header("SIGNAL HISTORY")

        # Select session
        sessions = SignalsSession.list_sessions()

        if not sessions:
            print(f"\n  {yellow('No sessions found')}")
            input("\nPress Enter to continue...")
            return

        print("\n📁 Select session:\n")
        for i, s in enumerate(sessions, 1):
            print(f"  {cyan(str(i))}. {s['name']} - {s['total_signals']} signals")

        choice = input(f"\n  Session #: ").strip()

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(sessions):
                session = SignalsSession.load(sessions[idx]["session_id"])
                self._display_signals(session)
        except (ValueError, IndexError):
            print(red("\n  Invalid selection"))
            time.sleep(1)

    def _display_signals(self, session: SignalsSession):
        """Display signals from session."""
        history = SignalHistory(session)
        formatter = SignalFormatter()

        while True:
            clear_screen()
            print_header(f"SIGNALS: {session.name}")

            print(f"\n  {cyan('[1]')} Last 10 signals")
            print(f"  {cyan('[2]')} Last 24 hours")
            print(f"  {cyan('[3]')} Today's signals")
            print(f"  {cyan('[4]')} High quality only (80+)")
            print(f"  {cyan('[5]')} By symbol")
            print(f"  {cyan('[6]')} Export to CSV")
            print()
            print(f"  {dim('[0]')} Back")
            print()

            choice = input(f"  {bold('Select option')}: ").strip()

            if choice == "0" or not choice:
                return

            signals = []

            if choice == "1":
                signals = history.get_last_n(10)
            elif choice == "2":
                signals = session.get_recent_signals(24)
            elif choice == "3":
                signals = session.get_today_signals()
            elif choice == "4":
                signals = history.get_high_quality(80)
            elif choice == "5":
                symbol = input_string("  Enter symbol (e.g., BTCUSDT)").upper()
                signals = history.get_by_symbol(symbol)
            elif choice == "6":
                filepath = session.export_csv()
                print(green(f"\n  ✅ Exported to: {filepath}"))
                input("\n  Press Enter to continue...")
                continue

            if not signals:
                print(f"\n  {yellow('No signals found')}")
                input("\n  Press Enter to continue...")
                continue

            # Display signals
            clear_screen()
            print(f"\n  Found {len(signals)} signal(s):\n")

            for s in signals[-20:]:  # Show max 20
                emoji = "📈" if s.direction == "LONG" else "📉"
                time_str = s.timestamp.strftime("%m-%d %H:%M")

                print(
                    f"  {emoji} {s.symbol:<12} {s.direction:<5} "
                    f"${s.price:<12.6f} Score: {s.score:>5.1f}  {dim(time_str)}"
                )

            if len(signals) > 20:
                print(f"\n  {dim(f'... and {len(signals) - 20} more')}")

            input("\n  Press Enter to continue...")

    def _view_recent_signals(self):
        """View recent signals from current session."""
        if not self.engine or not self.engine.session:
            print(f"\n  {yellow('No active session')}")
            input("\nPress Enter to continue...")
            return

        signals = self.engine.session.get_recent_signals(24)

        clear_screen()
        print_header("RECENT SIGNALS (24h)")

        if not signals:
            print(f"\n  {yellow('No signals in last 24 hours')}")
            input("\nPress Enter to continue...")
            return

        print()
        for s in signals[-15:]:
            emoji = "📈" if s.direction == "LONG" else "📉"
            time_str = s.timestamp.strftime("%H:%M:%S")

            print(
                f"  {emoji} {s.symbol:<12} {s.direction:<5} "
                f"${s.price:<12.8f} Score: {s.score:>5.1f}  {dim(time_str)}"
            )

        input("\n  Press Enter to continue...")

    # =========================================================================
    # STATISTICS
    # =========================================================================

    def _view_stats(self):
        """View session statistics."""
        clear_screen()
        print_header("SESSION STATISTICS")

        # Get session (from engine or select)
        if self.engine and self.engine.session:
            session = self.engine.session
        else:
            sessions = SignalsSession.list_sessions()

            if not sessions:
                print(f"\n  {yellow('No sessions found')}")
                input("\nPress Enter to continue...")
                return

            print("\n📁 Select session:\n")
            for i, s in enumerate(sessions, 1):
                print(f"  {cyan(str(i))}. {s['name']}")

            choice = input(f"\n  Session #: ").strip()

            try:
                idx = int(choice) - 1
                session = SignalsSession.load(sessions[idx]["session_id"])
            except (ValueError, IndexError):
                print(red("\n  Invalid selection"))
                time.sleep(1)
                return

        # Display stats
        stats = session.get_stats()

        print(f"\n  📁 Session: {bold(stats['name'])}")
        print(f"  {'─' * 40}")
        print(f"\n  📊 {bold('Activity')}")
        print(f"     Total Scans:    {stats['total_scans']}")
        print(f"     Total Signals:  {stats['total_signals']}")
        print(f"     Alerts Sent:    {stats['alerts_sent']}")
        print(f"\n  📈 {bold('By Direction')}")
        print(f"     LONG:  {stats['long_signals']}")
        print(f"     SHORT: {stats['short_signals']}")
        print(f"\n  📆 {bold('Recent')}")
        print(f"     Today:    {stats['today_signals']}")
        print(f"     Last 24h: {stats['last_24h_signals']}")

        # Top symbols
        if stats.get("top_symbols"):
            print(f"\n  🏆 {bold('Top Symbols')}")
            for symbol, count in stats["top_symbols"][:5]:
                print(f"     {symbol}: {count}")

        # By regime
        if stats.get("by_regime"):
            print(f"\n  🌍 {bold('By Regime')}")
            for regime, count in stats["by_regime"].items():
                print(f"     {regime}: {count}")

        input("\n  Press Enter to continue...")

    # =========================================================================
    # CONFIG
    # =========================================================================

    def _select_config(self):
        """Select configuration."""
        clear_screen()
        print_header("SELECT CONFIGURATION")

        configs = list_configs("signals")
        if not configs:
            configs = list_configs("paper")
        if not configs:
            configs = [
                "config_safe.yaml",
                "config_balanced.yaml",
                "config_aggressive.yaml",
            ]

        print("\n📋 Available Configurations:\n")

        for i, cfg in enumerate(configs, 1):
            marker = " ← current" if cfg == self.selected_config else ""
            print(f"  {cyan(str(i))}. {cfg}{dim(marker)}")

        choice = input(f"\n  Select config (0 to cancel): ").strip()

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(configs):
                self.selected_config = configs[idx]
                print(green(f"\n  ✓ Selected: {self.selected_config}"))
                time.sleep(1)
        except ValueError:
            pass

    # =========================================================================
    # ALERTS
    # =========================================================================

    def _test_alerts(self):
        """Test alert connections."""
        clear_screen()
        print_header("TEST ALERTS")

        print("\n  Testing alert connections...\n")

        # Test Telegram
        print(f"  📱 Telegram: ", end="", flush=True)
        try:
            from signalbolt.alerts.telegram import TelegramAlert

            alert = TelegramAlert()
            if alert.enabled:
                if alert.test_connection():
                    print(green("✅ Connected!"))
                else:
                    print(red("❌ Failed to send"))
            else:
                print(yellow("⚠️ Not configured"))
        except Exception as e:
            print(red(f"❌ Error: {e}"))

        # Test Discord
        print(f"  💬 Discord:  ", end="", flush=True)
        try:
            from signalbolt.alerts.discord import DiscordAlert

            alert = DiscordAlert()
            if alert.enabled:
                if alert.test_connection():
                    print(green("✅ Connected!"))
                else:
                    print(red("❌ Failed to send"))
            else:
                print(yellow("⚠️ Not configured"))
        except Exception as e:
            print(red(f"❌ Error: {e}"))

        print(f"\n  {dim('Configure alerts in .env file:')}")
        print(f"  {dim('  TELEGRAM_BOT_TOKEN=your_token')}")
        print(f"  {dim('  TELEGRAM_CHAT_ID=your_chat_id')}")
        print(f"  {dim('  DISCORD_WEBHOOK_URL=your_webhook')}")

        input("\n  Press Enter to continue...")

    # =========================================================================
    # SESSION MANAGEMENT
    # =========================================================================

    def _manage_sessions(self):
        """Manage signal sessions."""
        while True:
            clear_screen()
            print_header("MANAGE SESSIONS")

            sessions = SignalsSession.list_sessions()

            if not sessions:
                print(f"\n  {yellow('No sessions found')}")
                input("\nPress Enter to continue...")
                return

            print("\n📁 Signal Sessions:\n")

            for i, s in enumerate(sessions, 1):
                last_active = s.get("last_active", "Never")
                if last_active and last_active != "Never":
                    try:
                        dt = datetime.fromisoformat(last_active)
                        last_active = dt.strftime("%Y-%m-%d %H:%M")
                    except:
                        pass

                print(f"  {cyan(str(i))}. {s['name']}")
                print(f"     Signals: {s['total_signals']} | Last: {last_active}")

            print(f"\n  {cyan('[D]')} Delete session")
            print(f"  {cyan('[E]')} Export session")
            print()
            print(f"  {dim('[0]')} Back")
            print()

            choice = input(f"  {bold('Select option')}: ").strip().upper()

            if choice == "0" or not choice:
                return
            elif choice == "D":
                self._delete_session(sessions)
            elif choice == "E":
                self._export_session(sessions)

    def _delete_session(self, sessions: List[dict]):
        """Delete a session."""
        idx = input(f"\n  Session # to delete: ").strip()

        try:
            session_idx = int(idx) - 1
            if 0 <= session_idx < len(sessions):
                session_name = sessions[session_idx]["name"]

                if input_yes_no(f"\n  Delete '{session_name}'?", default=False):
                    session = SignalsSession.load(sessions[session_idx]["session_id"])
                    session.delete(confirm=True)
                    print(green(f"\n  ✓ Deleted"))
                    time.sleep(1)
        except (ValueError, IndexError):
            print(red("\n  Invalid selection"))
            time.sleep(1)

    def _export_session(self, sessions: List[dict]):
        """Export a session."""
        idx = input(f"\n  Session # to export: ").strip()

        try:
            session_idx = int(idx) - 1
            if 0 <= session_idx < len(sessions):
                session = SignalsSession.load(sessions[session_idx]["session_id"])
                filepath = session.export_csv()
                print(green(f"\n  ✓ Exported to: {filepath}"))
                input("\n  Press Enter to continue...")
        except (ValueError, IndexError):
            print(red("\n  Invalid selection"))
            time.sleep(1)

    # =========================================================================
    # ENGINE CONTROL
    # =========================================================================

    def _show_status(self):
        """Show engine status."""
        if self.engine:
            self.engine.print_status()
        else:
            print(f"\n  {yellow('No engine running')}")
        input("\nPress Enter to continue...")

    def _pause_engine(self):
        """Pause engine."""
        if self.engine:
            self.engine.pause()
            print(yellow("\n  ⏸️ Engine paused"))
        time.sleep(1)

    def _resume_engine(self):
        """Resume engine."""
        if self.engine:
            self.engine.resume()
            print(green("\n  ▶️ Engine resumed"))
        time.sleep(1)

    def _stop_engine(self):
        """Stop engine."""
        if self.engine:
            if input_yes_no("\n  Stop monitoring?", default=True):
                self.engine.stop()
                print(green("\n  🛑 Engine stopped"))
                time.sleep(1)

    def _exit_menu(self):
        """Exit menu."""
        if self.engine and self.engine.state != SignalsEngineState.STOPPED:
            if input_yes_no("\n  Stop engine before exiting?", default=True):
                self.engine.stop()

        self.running = False


# =============================================================================
# ENTRY POINT
# =============================================================================


def run_signals_menu():
    """Run signals-only menu."""
    menu = SignalsMenu()
    menu.run()


if __name__ == "__main__":
    run_signals_menu()
