"""
Live Trading Menu - UNDER CONSTRUCTION.

This module will be available in future versions.
"""

from signalbolt.cli.utils import (
    clear_screen,
    yellow,
    red,
    cyan,
)


def run_live_menu():
    """Run live trading menu (placeholder)."""

    clear_screen()

    print("")
    print(red("═" * 70))
    print(yellow("              ⚠️  LIVE TRADING - UNDER CONSTRUCTION").center(65))
    print(red("═" * 70))

    print("""
    
    🚧  This feature is not yet available.
    
    Live trading will be available in a future update.
    
    For now, please use:
    
      • Paper Trading  - Test strategies with virtual money
      • Coin Details   - Analyze coins before trading
      • Backtest       - Test strategies on historical data
    
    
    ⚠️  IMPORTANT NOTICE:
    
      Live trading involves REAL MONEY and significant risk.
      When this feature is released:
      
      • Start with small amounts ($50-100)
      • Never invest more than you can afford to lose
      • Monitor the bot regularly
      • Use API keys WITHOUT withdrawal permissions
    
    """)

    print(red("═" * 70))

    input(f"\n{cyan('Press Enter to return to main menu...')}")


if __name__ == "__main__":
    run_live_menu()
