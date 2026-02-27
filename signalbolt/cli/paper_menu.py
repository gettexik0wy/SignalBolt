"""
Paper trading CLI menu.

Interactive menu for paper trading:
- Start/stop sessions
- View positions
- View statistics
- Manage settings
"""

import sys
import time
from typing import Optional, List
from datetime import datetime
from pathlib import Path

from signalbolt.paper.engine import PaperEngine, EngineState
from signalbolt.paper.session import PaperSession
from signalbolt.core.config import Config, list_configs
from signalbolt.cli.utils import (
    print_header,
    print_divider,
    print_table,
    print_box,
    input_yes_no,
    input_string,
    input_number,
    green,
    red,
    yellow,
    cyan,
    bold,
    dim,
    format_usd,
    format_pct_colored,
    format_duration,
    clear_screen,
    status_emoji,
    pnl_emoji,
)
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.cli.paper")


# =============================================================================
# PAPER MENU
# =============================================================================


class PaperMenu:
    """
    Paper trading interactive menu.

    Usage:
        menu = PaperMenu()
        menu.run()
    """

    def __init__(self):
        """Initialize menu."""
        self.engine: Optional[PaperEngine] = None
        self.running = True
        self.selected_config = "config_safe.yaml"

    def run(self):
        """Run main menu loop."""
        while self.running:
            try:
                self._show_main_menu()
            except KeyboardInterrupt:
                print(f"\n\n{yellow('Interrupted. Use Exit to quit properly.')}")
                time.sleep(1)
            except Exception as e:
                print(f"\n{red(f'Error: {e}')}")
                input("\nPress Enter to continue...")

    # =========================================================================
    # MAIN MENU
    # =========================================================================

    def _show_main_menu(self):
        """Show main paper trading menu."""
        clear_screen()
        self._print_header()

        # Menu options based on engine state
        if self.engine and self.engine.state not in [
            EngineState.STOPPED,
            EngineState.IDLE,
        ]:
            self._show_running_menu()
        else:
            self._show_stopped_menu()

    def _print_header(self):
        """Print status header."""
        print(f"\n{'═' * 70}")

        if self.engine and self.engine.state not in [
            EngineState.STOPPED,
            EngineState.IDLE,
        ]:
            status = self.engine.get_status()
            state_emoji = (
                "🟢"
                if status["state"] == "running"
                else "🟡"
                if status["state"] == "paused"
                else "🔴"
            )

            print(
                f"  {state_emoji} {bold('PAPER TRADING')} │ "
                f"Session: {cyan(status['session'] or 'None')} │ "
                f"State: {status['state'].upper()}"
            )

            if status["portfolio"]:
                p = status["portfolio"]
                pnl_str = format_pct_colored(status["pnl_pct"])
                print(
                    f"  💰 Balance: {green(format_usd(p['balance']))} │ "
                    f"P&L: {pnl_str} │ "
                    f"Positions: {p['open_positions']}"
                )
        else:
            print(f"  🔴 {bold('PAPER TRADING')} │ {dim('Engine stopped')}")

        print(f"{'═' * 70}\n")

    def _show_stopped_menu(self):
        """Show menu when engine is stopped."""
        print(f"  {cyan('[1]')} 🚀 Start Trading")
        print(f"  {cyan('[2]')} 📁 Manage Sessions")
        print(f"  {cyan('[3]')} ⚙️  Select Config")
        print(f"  {cyan('[4]')} 📋 View Presets")
        print()
        print(f"  {dim('[0]')} 🚪 Exit to Main Menu")
        print()

        choice = input(f"  {bold('Select option')}: ").strip()

        if choice == "1":
            self._start_trading()
        elif choice == "2":
            self._manage_sessions()
        elif choice == "3":
            self._select_config()
        elif choice == "4":
            self._view_presets()
        elif choice == "0":
            self._exit_menu()

    def _show_running_menu(self):
        """Show menu when engine is running."""
        print(f"  {cyan('[1]')} 📊 Show Status")
        print(f"  {cyan('[2]')} 📈 View Positions")
        print(f"  {cyan('[3]')} 📉 View Statistics")
        print(f"  {cyan('[4]')} ⏸️  Pause/Resume")
        print(f"  {cyan('[5]')} 🚪 Close All Positions")
        print(f"  {cyan('[6]')} 🛑 Stop Engine")
        print()
        print(f"  {dim('[0]')} 🚪 Exit to Main Menu")
        print()

        choice = input(f"  {bold('Select option')}: ").strip()

        if choice == "1":
            self._show_status()
        elif choice == "2":
            self._show_positions()
        elif choice == "3":
            self._show_statistics()
        elif choice == "4":
            self._toggle_pause()
        elif choice == "5":
            self._close_all_positions()
        elif choice == "6":
            self._stop_engine()
        elif choice == "0":
            self._exit_menu()

    # =========================================================================
    # START TRADING
    # =========================================================================

    def _start_trading(self):
        """Start paper trading session."""
        clear_screen()
        print_header("START PAPER TRADING")

        # 1. Select config
        configs = list_configs("paper")

        if not configs:
            # Fallback to main configs dir
            configs = list_configs()

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

        # 2. Select or create session
        sessions = PaperSession.list_sessions()

        print("\n📁 Available sessions:")

        if sessions:
            for i, session in enumerate(sessions, 1):
                last_active = session.get("last_active", "Never")
                if last_active and last_active != "Never":
                    try:
                        dt = datetime.fromisoformat(last_active)
                        last_active = dt.strftime("%Y-%m-%d %H:%M")
                    except:
                        pass

                print(
                    f"  {cyan(str(i))}. {session['name']} "
                    f"({dim(f'last: {last_active}')})"
                )

            print(f"  {cyan(str(len(sessions) + 1))}. {green('+ Create new session')}")

            session_choice = input(
                f"\nSelect session (default: {len(sessions) + 1}): "
            ).strip()

            try:
                idx = int(session_choice) - 1 if session_choice else len(sessions)
            except ValueError:
                idx = len(sessions)

            if idx == len(sessions):
                # Create new
                session_name = input_string("Enter session name", min_length=1)
                create_new = True
            elif 0 <= idx < len(sessions):
                session_name = sessions[idx]["session_id"]
                create_new = False
            else:
                session_name = input_string("Enter session name", min_length=1)
                create_new = True
        else:
            print(f"  {dim('No sessions found')}")
            session_name = input_string("Enter new session name", min_length=1)
            create_new = True

        # 3. Get initial balance for new sessions
        initial_balance = 1000.0
        if create_new:
            balance_input = input(f"\nInitial balance (default: $1000): ").strip()
            if balance_input:
                try:
                    initial_balance = float(balance_input)
                except ValueError:
                    pass

        # 4. Confirmation
        print(f"\n{'─' * 40}")
        print(f"  Config:  {yellow(self.selected_config)}")
        print(
            f"  Session: {cyan(session_name)} {green('(new)') if create_new else dim('(existing)')}"
        )
        if create_new:
            print(f"  Balance: {green(format_usd(initial_balance))}")
        print(f"{'─' * 40}")

        if not input_yes_no("\nStart trading?", default=True):
            return

        # 5. Create engine and start
        print(f"\n🚀 Starting engine...")

        self.engine = PaperEngine(self.selected_config)

        success = self.engine.start_session(
            session_name=session_name,
            create_new=create_new,
            initial_balance=initial_balance,
        )

        if not success:
            print(red("\n❌ Failed to start session"))
            input("\nPress Enter to continue...")
            return

        print(green("\n✅ Session loaded successfully!"))
        print(f"\n{yellow('Starting trading loop...')}")
        print(dim("Press Ctrl+C to return to menu\n"))

        time.sleep(1)

        # Run engine (blocking)
        try:
            self.engine.run()
        except KeyboardInterrupt:
            print(f"\n\n{yellow('Stopping engine...')}")
            self.engine.stop()

        input("\nPress Enter to continue...")

    # =========================================================================
    # SESSION MANAGEMENT
    # =========================================================================

    def _manage_sessions(self):
        """Manage saved sessions."""
        while True:
            clear_screen()
            print_header("SESSION MANAGEMENT")

            sessions = PaperSession.list_sessions()

            if not sessions:
                print(f"\n📭 No sessions found")
                print(f"\n{dim('Start trading to create a session.')}")
                input("\nPress Enter to go back...")
                return

            # Show sessions table
            print(f"\n📁 Saved Sessions:\n")

            headers = ["#", "Name", "Status", "Trades", "Created"]
            rows = []

            for i, session in enumerate(sessions, 1):
                created = session.get("created_at", "")
                if created:
                    try:
                        dt = datetime.fromisoformat(created)
                        created = dt.strftime("%Y-%m-%d")
                    except:
                        pass

                rows.append(
                    [
                        i,
                        session.get("name", session.get("session_id", "Unknown")),
                        session.get("status", "unknown"),
                        session.get("total_trades", 0),
                        created,
                    ]
                )

            print_table(headers, rows, ["r", "l", "l", "r", "l"])

            # Options
            print(f"\n  {cyan('[1]')} 👁️  View Details")
            print(f"  {cyan('[2]')} 🗑️  Delete Session")
            print(f"  {cyan('[3]')} 📤 Export Trades")
            print()
            print(f"  {dim('[0]')} ← Back")
            print()

            choice = input(f"  {bold('Select option')}: ").strip()

            if choice == "0" or not choice:
                return
            elif choice == "1":
                self._view_session_details(sessions)
            elif choice == "2":
                self._delete_session(sessions)
            elif choice == "3":
                self._export_session(sessions)

    def _view_session_details(self, sessions: List[dict]):
        """View detailed session info."""
        idx = input(f"\n  Enter session number: ").strip()

        try:
            session_idx = int(idx) - 1
            if 0 <= session_idx < len(sessions):
                session_id = sessions[session_idx]["session_id"]
                session = PaperSession.load(session_id)

                if session:
                    clear_screen()
                    summary = session.get_summary()

                    print(f"\n{'=' * 60}")
                    print(f"{'SESSION DETAILS':^60}")
                    print(f"{'=' * 60}")

                    print(f"\n  ID:       {summary['session_id']}")
                    print(f"  Name:     {summary['name']}")
                    print(f"  Status:   {summary['status']}")
                    print(f"  Created:  {summary['created_at']}")
                    print(f"  Scans:    {summary['total_scans']}")
                    print(f"  Runtime:  {summary['total_runtime_minutes']:.1f} min")

                    if "portfolio" in summary:
                        p = summary["portfolio"]
                        print(f"\n  {'─' * 40}")
                        print(f"  Balance:    ${p.get('total_balance', 0):.2f}")
                        print(f"  Positions:  {p.get('open_positions', 0)}")

                    print(f"\n{'=' * 60}")

                input("\nPress Enter to continue...")
        except (ValueError, IndexError):
            print(red("\n  Invalid selection"))
            time.sleep(1)

    def _delete_session(self, sessions: List[dict]):
        """Delete a session."""
        idx = input(f"\n  Enter session number to delete: ").strip()

        try:
            session_idx = int(idx) - 1
            if 0 <= session_idx < len(sessions):
                session_name = sessions[session_idx].get(
                    "name", sessions[session_idx]["session_id"]
                )

                if input_yes_no(f"\n  Delete session '{session_name}'?", default=False):
                    session_id = sessions[session_idx]["session_id"]
                    session = PaperSession.load(session_id)
                    if session:
                        session.delete(confirm=True)
                        print(green(f"\n  ✅ Session '{session_name}' deleted"))
                    time.sleep(1)
        except (ValueError, IndexError):
            print(red("\n  Invalid selection"))
            time.sleep(1)

    def _export_session(self, sessions: List[dict]):
        """Export session to CSV."""
        idx = input(f"\n  Enter session number to export: ").strip()

        try:
            session_idx = int(idx) - 1
            if 0 <= session_idx < len(sessions):
                session_id = sessions[session_idx]["session_id"]
                session = PaperSession.load(session_id)

                if session:
                    filepath = session.export_trades("csv")
                    print(green(f"\n  ✅ Exported to: {filepath}"))

                input("\nPress Enter to continue...")
        except (ValueError, IndexError):
            print(red("\n  Invalid selection"))
            time.sleep(1)

    # =========================================================================
    # CONFIG SELECTION
    # =========================================================================

    def _select_config(self):
        """Select configuration."""
        clear_screen()
        print_header("SELECT CONFIGURATION")

        configs = list_configs("paper")
        if not configs:
            configs = list_configs()
        if not configs:
            configs = [
                "config_safe.yaml",
                "config_balanced.yaml",
                "config_aggressive.yaml",
            ]

        print(f"\n📋 Available Configurations:\n")

        for i, cfg in enumerate(configs, 1):
            marker = " ← current" if cfg == self.selected_config else ""

            # Determine risk level
            if "safe" in cfg.lower():
                risk = f"{green('Low Risk')}"
                desc = "Conservative settings, smaller positions"
            elif "aggressive" in cfg.lower():
                risk = f"{red('High Risk')}"
                desc = "Aggressive settings, larger positions"
            else:
                risk = f"{yellow('Medium Risk')}"
                desc = "Balanced settings"

            print(f"  {cyan(str(i))}. {bold(cfg)}{dim(marker)}")
            print(f"      Risk: {risk} - {dim(desc)}")
            print()

        choice = input(f"\n  Select config (0 to cancel): ").strip()

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(configs):
                self.selected_config = configs[idx]
                print(green(f"\n  ✅ Selected: {self.selected_config}"))
                time.sleep(1)
        except ValueError:
            pass

    # =========================================================================
    # VIEW PRESETS
    # =========================================================================

    def _view_presets(self):
        """View regime presets."""
        clear_screen()
        print_header("REGIME PRESETS")

        print(f"""
  SignalBolt adapts to market conditions using regime detection.

  {bold("BULL Market")} {green("🟢")}
  ─────────────────────────────────────
  • Looser stop-loss (trend-following)
  • Higher position sizes
  • More aggressive entries
  
  {bold("BEAR Market")} {red("🔴")}
  ─────────────────────────────────────
  • Tighter stop-loss (capital protection)
  • Smaller position sizes
  • More selective entries
  
  {bold("RANGE Market")} {yellow("🟡")}
  ─────────────────────────────────────
  • Balanced settings
  • Normal position sizes
  • Standard entry criteria
  
  {bold("CRASH Mode")} {red("⚠️")}
  ─────────────────────────────────────
  • Very tight stop-loss
  • Minimal position sizes
  • May pause trading entirely

  {dim("The bot detects regime automatically based on BTC price action.")}
""")

        input("\nPress Enter to continue...")

    # =========================================================================
    # ENGINE CONTROLS (when running)
    # =========================================================================

    def _show_status(self):
        """Show engine status."""
        clear_screen()

        if self.engine:
            self.engine.print_status()
        else:
            print("\n  ❓ No engine running")

        input("\nPress Enter to continue...")

    def _show_positions(self):
        """Show current positions."""
        clear_screen()

        if self.engine:
            self.engine.print_positions()
        else:
            print("\n  ❓ No engine running")

        input("\nPress Enter to continue...")

    def _show_statistics(self):
        """Show trading statistics."""
        clear_screen()
        print_header("TRADING STATISTICS")

        if not self.engine or not self.engine.session:
            print("\n  ❓ No statistics available")
            input("\nPress Enter to continue...")
            return

        status = self.engine.get_status()
        portfolio = self.engine.portfolio

        if portfolio:
            initial = portfolio.initial_balance
            current = portfolio.total_balance
            pnl = current - initial
            pnl_pct = (pnl / initial * 100) if initial > 0 else 0

            print(f"\n  {bold('Portfolio')}")
            print(f"  {'─' * 40}")
            print(f"  Initial Balance:   {format_usd(initial)}")
            print(f"  Current Balance:   {format_usd(current)}")
            print(
                f"  Total P&L:         {format_pct_colored(pnl_pct)} ({format_usd(pnl)})"
            )
            print(f"  Open Positions:    {portfolio.open_position_count}")

            print(f"\n  {bold('Activity')}")
            print(f"  {'─' * 40}")
            print(f"  Total Scans:       {status['scan_count']}")
            print(f"  Signals Found:     {status['signal_count']}")
            print(f"  Trades Executed:   {status['trade_count']}")
            print(f"  Runtime:           {status['runtime_minutes']:.1f} minutes")

        print()
        input("\nPress Enter to continue...")

    def _toggle_pause(self):
        """Toggle pause/resume."""
        if not self.engine:
            return

        if self.engine.state == EngineState.PAUSED:
            self.engine.resume()
            print(green("\n  ▶️ Engine resumed"))
        elif self.engine.state == EngineState.RUNNING:
            self.engine.pause()
            print(yellow("\n  ⏸️ Engine paused"))
        else:
            print(dim(f"\n  Engine is {self.engine.state.value}"))

        time.sleep(1)

    def _close_all_positions(self):
        """Close all positions."""
        if not self.engine:
            return

        positions = self.engine.open_positions

        if not positions:
            print("\n  📭 No open positions")
            input("\nPress Enter to continue...")
            return

        print(f"\n  ⚠️ You have {len(positions)} open position(s)")

        if input_yes_no("  Close all positions?", default=False):
            results = self.engine.close_all_positions("manual_close_all")
            print(green(f"\n  ✅ Closed {len(results)} position(s)"))

        input("\nPress Enter to continue...")

    def _stop_engine(self):
        """Stop the engine."""
        if not self.engine:
            return

        if input_yes_no("\n  Stop paper trading?", default=True):
            self.engine.stop()
            print(green("\n  🛑 Engine stopped"))
            time.sleep(1)

    def _exit_menu(self):
        """Exit to main menu."""
        if self.engine and self.engine.state not in [
            EngineState.STOPPED,
            EngineState.IDLE,
        ]:
            if input_yes_no("\n  Stop engine before exiting?", default=True):
                self.engine.stop()

        self.running = False


# =============================================================================
# ENTRY POINT
# =============================================================================


def run_paper_menu():
    """Run paper trading menu."""
    menu = PaperMenu()
    menu.run()


if __name__ == "__main__":
    run_paper_menu()
