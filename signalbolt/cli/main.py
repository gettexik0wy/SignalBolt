"""
SignalBolt CLI - Main entry point.

Interactive command-line interface for SignalBolt trading bot.
"""

import sys
import signal
from typing import Optional

from signalbolt.cli.utils import (
    clear_screen, print_header, print_menu, get_menu_choice,
    green, red, yellow, cyan, bold, dim,
    print_box, print_divider
)
from signalbolt.cli.paper_menu import run_paper_menu
from signalbolt.utils.logger import get_logger, setup_logging

log = get_logger('signalbolt.cli')


# =============================================================================
# ASCII ART
# =============================================================================

LOGO = r"""
   _____ _                   _ ____        _ _   
  / ____(_)                 | |  _ \      | | |  
 | (___  _  __ _ _ __   __ _| | |_) | ___ | | |_ 
  \___ \| |/ _` | '_ \ / _` | |  _ < / _ \| | __|
  ____) | | (_| | | | | (_| | | |_) | (_) | | |_ 
 |_____/|_|\__, |_| |_|\__,_|_|____/ \___/|_|\__|
            __/ |                                
           |___/                                 
"""

VERSION = "1.0.0"
TAGLINE = "Crypto Trading Bot with Regime-Aware Strategies"


# =============================================================================
# SIGNAL HANDLERS
# =============================================================================

def setup_signal_handlers():
    """Setup graceful shutdown handlers."""
    
    def handle_sigint(signum, frame):
        print("\n\n" + yellow("Received interrupt signal. Shutting down..."))
        sys.exit(0)
    
    def handle_sigterm(signum, frame):
        print("\n\n" + yellow("Received termination signal. Shutting down..."))
        sys.exit(0)
    
    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigterm)


# =============================================================================
# MAIN MENU
# =============================================================================

class MainMenu:
    """
    Main CLI menu.
    
    Entry point for all SignalBolt functionality.
    """
    
    def __init__(self):
        """Initialize main menu."""
        self.running = True
    
    def run(self):
        """Run main menu loop."""
        
        setup_signal_handlers()
        setup_logging()
        
        while self.running:
            self._show_main_menu()
    
    def _show_main_menu(self):
        """Display main menu."""
        
        clear_screen()
        self._print_header()
        
        options = [
            ('paper', '📊 Paper Trading'),
            ('signals', '📡 Signals Only'),
            ('backtest', '🔬 Backtest'),
            ('coin', '🔍 Coin Details'),
            ('config', '⚙️  Configuration'),
            ('status', 'ℹ️  System Status'),
            ('exit', '🚪 Exit'),
        ]
        
        print_menu("MAIN MENU", options, show_back=False)
        
        choice = get_menu_choice(options, show_back=False)
        
        if choice:
            self._handle_choice(choice)
    
    def _handle_choice(self, choice: str):
        """Handle menu selection."""
        
        handlers = {
            'paper': self._run_paper_trading,
            'signals': self._run_signals_only,
            'backtest': self._run_backtest,
            'coin': self._run_coin_details,
            'config': self._run_config_menu,
            'status': self._show_status,
            'exit': self._exit,
        }
        
        handler = handlers.get(choice)
        
        if handler:
            handler()
        else:
            print(red(f"Unknown option: {choice}"))
            input("\nPress Enter to continue...")
    
    # =========================================================================
    # MENU HANDLERS
    # =========================================================================
    
    def _run_paper_trading(self):
        """Run paper trading menu."""
        run_paper_menu()
    
    def _run_signals_only(self):
        """Run signals-only mode."""
        clear_screen()
        print_header("SIGNALS ONLY MODE")
        print("\n" + yellow("⚠️ Coming soon in future update!"))
        print("\nThis mode will:")
        print("  • Generate trading signals")
        print("  • Send alerts via Telegram/Discord")
        print("  • NOT execute any trades")
        input("\nPress Enter to continue...")
    
    def _run_backtest(self):
        """Run backtest menu."""
        clear_screen()
        print_header("BACKTEST")
        print("\n" + yellow("⚠️ Coming soon in future update!"))
        print("\nThis will include:")
        print("  • Historical backtesting")
        print("  • Monte Carlo validation")
        print("  • Walk-forward analysis")
        print("  • Strategy comparison")
        input("\nPress Enter to continue...")
    
    def _run_coin_details(self):
        """Run coin details analyzer."""
        from signalbolt.cli.coin_details import run_coin_details
        run_coin_details()
    
    def _run_config_menu(self):
        """Run configuration menu."""
        from signalbolt.cli.config_menu import run_config_menu
        run_config_menu()
    
    def _show_status(self):
        """Show system status."""
        
        clear_screen()
        print_header("SYSTEM STATUS")
        
        # Check components
        checks = []
        
        # Check exchange connection
        try:
            from signalbolt.exchange.client import get_exchange
            exchange = get_exchange()
            ticker = exchange.get_ticker('BTCUSDT')
            btc_price = ticker.last_price if ticker else 0
            checks.append(('Exchange Connection', True, f"BTC: ${btc_price:,.2f}"))
        except Exception as e:
            checks.append(('Exchange Connection', False, str(e)))
        
        # Check config
        try:
            from signalbolt.core.config import get_config, list_configs
            configs = list_configs()
            checks.append(('Configuration', True, f"{len(configs)} configs available"))
        except Exception as e:
            checks.append(('Configuration', False, str(e)))
        
        # Check data directory
        try:
            from pathlib import Path
            data_dir = Path('data/paper_sessions')
            data_dir.mkdir(parents=True, exist_ok=True)
            checks.append(('Data Directory', True, str(data_dir)))
        except Exception as e:
            checks.append(('Data Directory', False, str(e)))
        
        # Check market data
        try:
            market_dir = Path('market_data/parquet')
            if market_dir.exists():
                files = list(market_dir.glob('*.parquet'))
                checks.append(('Market Data Cache', True, f"{len(files)} files"))
            else:
                checks.append(('Market Data Cache', True, "Empty (will download on demand)"))
        except Exception as e:
            checks.append(('Market Data Cache', False, str(e)))
        
        # Print results
        print("\n")
        
        for name, ok, detail in checks:
            emoji = "✅" if ok else "❌"
            status = green("OK") if ok else red("FAIL")
            print(f"  {emoji} {name}: {status}")
            print(f"     {dim(detail)}")
            print()
        
        # System info
        print_divider()
        print(f"\n  📦 Version: {VERSION}")
        print(f"  🐍 Python: {sys.version.split()[0]}")
        
        import platform
        print(f"  💻 Platform: {platform.system()} {platform.release()}")
        
        input("\n\nPress Enter to continue...")
    
    def _exit(self):
        """Exit application."""
        
        clear_screen()
        print(f"\n{cyan(LOGO)}")
        print(f"\n  {bold('Thank you for using SignalBolt!')}")
        print(f"  {dim('Trade safely and responsibly.')}\n")
        
        self.running = False
    
    # =========================================================================
    # HELPERS
    # =========================================================================
    
    def _print_header(self):
        """Print application header."""
        
        print(cyan(LOGO))
        print(f"  {bold(f'v{VERSION}')} - {TAGLINE}")
        print_divider('─')


# =============================================================================
# ENTRY POINTS
# =============================================================================

def main():
    """Main entry point."""
    
    try:
        menu = MainMenu()
        menu.run()
    except KeyboardInterrupt:
        print("\n\n" + yellow("Goodbye!"))
        sys.exit(0)
    except Exception as e:
        log.error(f"Fatal error: {e}")
        print(red(f"\n❌ Fatal error: {e}"))
        sys.exit(1)


def cli():
    """CLI entry point (for setup.py console_scripts)."""
    main()


if __name__ == '__main__':
    main()