"""
SignalBolt - Main Entry Point

Enhanced CLI launcher with menu system.
"""

import os
import sys
import time

# =============================================================================
# PATH SETUP - Must be FIRST before any signalbolt imports
# =============================================================================

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# =============================================================================
# LOAD .ENV - Before any signalbolt imports!
# =============================================================================

from dotenv import load_dotenv
from pathlib import Path

_env_path = Path(PROJECT_ROOT) / ".env"
if _env_path.exists():
    load_dotenv(_env_path, override=True)
# =============================================================================
# TERMINAL TITLE
# =============================================================================


def set_terminal_title(title: str):
    """Set terminal window title."""
    if os.name == "nt":  # Windows
        os.system(f"title {title}")
    else:  # Linux/Mac
        print(f"\033]0;{title}\007", end="", flush=True)


set_terminal_title("SignalBolt v1.0.0 - Crypto Trading Bot [BETA]")

# =============================================================================
# STARTUP CHECKS
# =============================================================================


def print_startup_message(message: str, status: str = "..."):
    """Print startup status message."""
    print(f"  {message} {status}", end="\r")
    sys.stdout.flush()


def check_dependencies():
    """Check if required packages are installed."""
    print("\n  🔍 Checking dependencies...\n")

    required = [
        ("pandas", "pandas"),
        ("numpy", "numpy"),
        ("ccxt", "ccxt"),
        ("pyyaml", "yaml"),
        ("colorama", "colorama"),
        ("requests", "requests"),
        ("pandas_ta", "pandas_ta"),
        ("python-dotenv", "dotenv"),
    ]

    missing = []

    for pip_name, import_name in required:
        print_startup_message(f"Checking {pip_name}...", "")
        time.sleep(0.1)  # Small delay for visual effect
        try:
            __import__(import_name)
            print_startup_message(f"Checking {pip_name}...", "✅")
            print()  # New line
        except ImportError:
            print_startup_message(f"Checking {pip_name}...", "❌")
            print()
            missing.append(pip_name)

    if missing:
        print(f"\n  ❌ Missing packages: {', '.join(missing)}")
        print(f"  Run: pip install {' '.join(missing)}\n")
        sys.exit(1)

    print("\n  ✅ All dependencies OK!\n")
    time.sleep(0.5)


def ensure_directories():
    """Ensure required directories exist."""
    from pathlib import Path

    dirs = [
        "data/paper_sessions",
        "data/live_sessions",
        "data/signal_sessions",
        "data/coin_analysis",
        "logs",
        "market_data/parquet",
        "market_data/csv_cache",
        "configs",
    ]

    for dir_path in dirs:
        Path(dir_path).mkdir(parents=True, exist_ok=True)


# =============================================================================
# IMPORTS (after dependency check would normally go, but we need colorama first)
# =============================================================================

from pathlib import Path
from datetime import datetime, timedelta
from colorama import init, Fore, Style
import warnings

warnings.filterwarnings("ignore", category=SyntaxWarning)
init(autoreset=True)

# =============================================================================
# MARKET DATA CACHE
# =============================================================================

_market_cache = {
    "regime": None,
    "emoji": None,
    "color": None,
    "btc_price": None,
    "last_update": None,
    "cache_duration": 60,  # seconds
}


def is_cache_valid():
    """Check if market data cache is still valid."""
    if _market_cache["last_update"] is None:
        return False
    elapsed = (datetime.now() - _market_cache["last_update"]).total_seconds()
    return elapsed < _market_cache["cache_duration"]


def get_cached_market_data():
    """Get market data from cache or fetch new."""
    if is_cache_valid():
        return (
            _market_cache["regime"],
            _market_cache["emoji"],
            _market_cache["color"],
            _market_cache["btc_price"],
        )

    # Fetch new data
    regime, emoji, color = detect_current_regime()
    btc_price = get_btc_price()

    # Update cache
    _market_cache["regime"] = regime
    _market_cache["emoji"] = emoji
    _market_cache["color"] = color
    _market_cache["btc_price"] = btc_price
    _market_cache["last_update"] = datetime.now()

    return regime, emoji, color, btc_price


def fetch_market_data_with_message():
    """Fetch market data with loading message (for initial load)."""
    print(f"\n  {Fore.CYAN}📡 Fetching market data...{Style.RESET_ALL}", end="")
    sys.stdout.flush()

    regime, emoji, color, btc_price = get_cached_market_data()

    print(f"\r  {Fore.GREEN}📡 Market data loaded!   {Style.RESET_ALL}")
    time.sleep(0.1)

    return regime, emoji, color, btc_price


# =============================================================================
# ASCII ART & BRANDING
# =============================================================================


def show_banner():
    """Display SignalBolt banner."""
    ascii_art = rf"""
{Fore.RED}
   ,-,--.   .=-.-.    _,---.  .-._         ,---.                              _,.---._           ,--.--------.  
 ,-.'-  _\ /==/_ /_.='.'-,  \/==/ \  .-._.--.'  \       _.-.       _..---.  ,-.' , -  `.    _.-./==/,  -   , -\ 
/==/_ ,_.'|==|, |/==.'-     /|==|, \/ /, |==\-/\ \    .-,.'|     .' .'.-. \/==/_,  ,  - \ .-,.'|\==\.-.  - ,-./ 
\==\  \   |==|  /==/ -   .-' |==|-  \|  |/==/-|_\ |  |==|, |    /==/- '=' /==|   .=.     |==|, | `--`\==\- \    
 \==\ -\  |==|- |==|_   /_,-.|==| ,  | -|\==\,   - \ |==|- |    |==|-,   '|==|_ : ;=:  - |==|- |      \==\_ \   
 _\==\ ,\ |==| ,|==|  , \_.' )==| -   _ |/==/ -   ,| |==|, |    |==|  .=. \==| , '='     |==|, |      |==|- |   
/==/\/ _ ||==|- \==\-  ,    (|==|  /\ , /==/-  /\ - \|==|- `-._ /==/- '=' ,\==\ -    ,_ /|==|- `-._   |==|, |   
\==\ - , //==/. //==/ _  ,  //==/, | |- \==\ _.\=\.-'/==/ - , ,/==|   -   / '.='. -   .' /==/ - , ,/  /==/ -/   
 `--`---' `--`-` `--`------' `--`./  `--``--`        `--`-----'`-._`.___,'    `--`--''   `--`-----'   `--`--`   

  {Fore.LIGHTRED_EX}v1.0.0 - Crypto Trading Bot [BETA]{Fore.CYAN}
{Style.RESET_ALL}"""

    print(ascii_art)


def show_warning():
    """Display risk warning and require acknowledgment."""
    warning = f"""
{Fore.LIGHTYELLOW_EX}⚠️  WARNING! SignalBolt is an experimental trading bot.

{Fore.WHITE}• You can LOSE ALL YOUR MONEY
• Trading cryptocurrency is HIGHLY RISKY
• Backtests are NOT guarantees of future results
• This is NOT financial advice
• Use at your own risk - Start with paper trading!

{Fore.LIGHTBLACK_EX}Suggestions? Errors? Report them to: https://github.com/gettexik0wy/signalbolt/issues 
{Style.RESET_ALL}"""

    print(warning)
    input(f"{Fore.YELLOW}Press Enter to acknowledge and continue...{Style.RESET_ALL}")


# =============================================================================
# MARKET STATUS
# =============================================================================


def detect_current_regime():
    """
    Detect current market regime for display.

    Returns:
        Tuple[str, str, str]: (regime_name, emoji, color)
    """
    try:
        import requests

        # Get BTC price from Binance
        url = "https://api.binance.com/api/v3/klines"
        params = {"symbol": "BTCUSDT", "interval": "1d", "limit": 30}

        response = requests.get(url, params=params, timeout=5)
        data = response.json()

        if not data or len(data) < 30:
            return "UNKNOWN", "❓", Fore.WHITE

        # Calculate price changes
        first_price = float(data[0][4])  # 30 days ago
        week_ago_price = float(data[-7][4])  # 7 days ago
        last_price = float(data[-1][4])  # Latest

        change_30d = ((last_price - first_price) / first_price) * 100
        change_7d = ((last_price - week_ago_price) / week_ago_price) * 100

        # Simple regime classification
        if change_7d < -15:
            return "CRASH", "🔴", Fore.RED
        elif change_30d > 20:
            return "BULL", "🟢", Fore.GREEN
        elif change_30d < -15:
            return "BEAR", "🔴", Fore.RED
        else:
            return "RANGE", "🟡", Fore.YELLOW

    except Exception:
        return "UNKNOWN", "❓", Fore.WHITE


def get_btc_price():
    """Get current BTC price."""
    try:
        import requests

        response = requests.get(
            "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=5
        )
        data = response.json()
        return float(data["price"])
    except:
        return None


# =============================================================================
# STATUS BAR
# =============================================================================


def show_status_bar():
    """Display current status bar."""
    now = datetime.now()

    # Use cached market data
    regime, emoji, color, btc_price = get_cached_market_data()

    # Get current config - with safe fallback
    config_name = "default"
    try:
        from signalbolt.core.config import get_config

        config = get_config()
        if config:
            config_name = (
                getattr(config, "_config_name", None)
                or getattr(config, "name", None)
                or "config_balanced"
            )
    except Exception:
        config_name = "not loaded"

    # Format values safely
    date_str = now.strftime("%Y-%m-%d %H:%M:%S")
    btc_str = f"${btc_price:,.2f}" if btc_price else "Unavailable"
    regime_str = f"{emoji} {regime}"
    config_str = str(config_name) if config_name else "unknown"

    status = f"""
{Fore.RED}¸,ø¤°`°¤ø,¸¸,ø¤°`°¤ø,¸¸,ø¤°`°¤ø,¸¸,ø¤°`°¤ø,¸¸¸¸,ø¤°`°¤ø,¸¸¸¸,ø¤°`°¤ø,¸¸¸¸,ø¤°`°¤ø,¸¸¸¸,ø¤°`°¤ø,¸¸

  {Fore.WHITE}                                  📅 {date_str:<67}
  {Fore.WHITE}                                  📊 Market: {color}{regime_str:<60}
  {Fore.WHITE}                                  ₿  BTC:    {Fore.GREEN}{btc_str:<60}
  {Fore.WHITE}                                  ⚙️  Config: {Fore.YELLOW}{config_str:<60}
{Style.RESET_ALL}"""

    print(status)


# =============================================================================
# MAIN MENU
# =============================================================================


def show_main_menu():
    """Display main menu."""
    menu = f"""
                                                                              
  {Fore.GREEN}                      [1]{Fore.WHITE} Live Trading           {Fore.LIGHTBLACK_EX}(Real money - DANGEROUS){Fore.RED}                         
  {Fore.GREEN}                      [2]{Fore.WHITE} Paper Trading          {Fore.LIGHTBLACK_EX}(Recommended - Virtual money){Fore.RED}                    
  {Fore.GREEN}                      [3]{Fore.WHITE} Signals Only           {Fore.LIGHTBLACK_EX}(No trading, alerts only){Fore.RED}                        
  {Fore.GREEN}                      [4]{Fore.WHITE} Backtest               {Fore.LIGHTBLACK_EX}(Test strategy on history){Fore.RED}                       
  {Fore.GREEN}                      [5]{Fore.WHITE} Coin Details           {Fore.LIGHTBLACK_EX}(Analyze single coin){Fore.RED}                            
  {Fore.GREEN}                      [6]{Fore.WHITE} Configuration          {Fore.LIGHTBLACK_EX}(View/edit settings){Fore.RED}                             
  {Fore.GREEN}                      [7]{Fore.WHITE} System Status          {Fore.LIGHTBLACK_EX}(Check components){Fore.RED}                               
  {Fore.GREEN}                      [8]{Fore.WHITE} Help & Documentation   {Fore.LIGHTBLACK_EX}(Learn how to use){Fore.RED}                               
  {Fore.GREEN}                      [9]{Fore.WHITE} About & Info           {Fore.LIGHTBLACK_EX}(Version, changelog, credits){Fore.RED}                    
  {Fore.GREEN}                      [0]{Fore.WHITE} Exit{Fore.RED}                    


{Fore.RED}¸,ø¤°`°¤ø,¸¸,ø¤°`°¤ø,¸¸,ø¤°`°¤ø,¸¸,ø¤°`°¤ø,¸¸¸¸,ø¤°`°¤ø,¸¸¸¸,ø¤°`°¤ø,¸¸¸¸,ø¤°`°¤ø,¸¸¸¸,ø¤°`°¤ø,¸¸
{Style.RESET_ALL}"""

    print(menu)


# =============================================================================
# INTERRUPT HANDLER FOR SUBMENUS
# =============================================================================


class SubMenuInterrupt(Exception):
    """Exception raised when user wants to return to previous menu."""

    pass


def run_with_interrupt_handler(func, *args, **kwargs):
    """
    Run a function with Ctrl+C handling.
    In submenus, Ctrl+C returns to previous menu instead of exiting.

    Args:
        func: Function to run
        *args, **kwargs: Arguments to pass to function

    Returns:
        Result of function or None if interrupted
    """
    try:
        return func(*args, **kwargs)
    except KeyboardInterrupt:
        print(f"\n\n{Fore.YELLOW}⬅️  Returning to previous menu...{Style.RESET_ALL}\n")
        time.sleep(0.5)
        return None
    except SubMenuInterrupt:
        return None


def confirm_exit():
    """Ask user to confirm exit."""
    print(
        f"\n{Fore.YELLOW}Are you sure you want to exit? (y/n): {Style.RESET_ALL}",
        end="",
    )
    try:
        response = input().strip().lower()
        return response in ("y", "yes")
    except (KeyboardInterrupt, EOFError):
        return True


# =============================================================================
# MENU HANDLERS
# =============================================================================
def live_menu():

    def _run_live():
        from signalbolt.cli.live_menu import run_live_menu

        run_live_menu()

    try:
        run_with_interrupt_handler(_run_live)
    except Exception as e:
        print(f"\n{Fore.RED}Error: {e}{Style.RESET_ALL}\n")
        import traceback

        traceback.print_exc()


def start_paper_trading():
    """Start paper trading mode."""
    print(f"\n{Fore.GREEN}Starting Paper Trading...{Style.RESET_ALL}\n")

    def _run_paper():
        from signalbolt.cli.paper_menu import run_paper_menu

        run_paper_menu()

    try:
        run_with_interrupt_handler(_run_paper)
    except Exception as e:
        print(f"\n{Fore.RED}Error: {e}{Style.RESET_ALL}\n")
        import traceback

        traceback.print_exc()

    input(f"\n{Fore.CYAN}Press Enter to return to main menu...{Style.RESET_ALL}")


def start_signals_only():
    """Start signals-only mode."""

    def _run_signals():
        from signalbolt.cli.signals_menu import run_signals_menu

        run_signals_menu()

    try:
        run_with_interrupt_handler(_run_signals)
    except Exception as e:
        print(f"\n{Fore.RED}Error: {e}{Style.RESET_ALL}\n")
        import traceback

        traceback.print_exc()

    input(f"\n{Fore.CYAN}Press Enter to return to main menu...{Style.RESET_ALL}")


def run_backtest():
    """Run backtest menu."""

    def _run_backtest():
        from signalbolt.cli.backtest_menu import run_backtest_menu

        run_backtest_menu()

    try:
        run_with_interrupt_handler(_run_backtest)
    except Exception as e:
        print(f"\n{Fore.RED}Error: {e}{Style.RESET_ALL}\n")
        import traceback

        traceback.print_exc()


def analyze_coin():
    """Run coin details analyzer."""

    def _run_coin():
        from signalbolt.cli.coin_details import run_coin_details

        run_coin_details()

    try:
        run_with_interrupt_handler(_run_coin)
    except Exception as e:
        print(f"\n{Fore.RED}Error: {e}{Style.RESET_ALL}\n")
        import traceback

        traceback.print_exc()


def manage_config():
    """Run configuration menu."""

    def _run_config():
        from signalbolt.cli.config_menu import run_config_menu

        run_config_menu()

    try:
        run_with_interrupt_handler(_run_config)
    except Exception as e:
        print(f"\n{Fore.RED}Error: {e}{Style.RESET_ALL}\n")
        import traceback

        traceback.print_exc()

    input(f"\n{Fore.CYAN}Press Enter to return to main menu...{Style.RESET_ALL}")


def show_system_status():
    """Show system status."""
    clear_screen()
    print(f"\n{Fore.CYAN}{'═' * 80}")
    print(f"{Fore.WHITE}{'SYSTEM STATUS':^80}")
    print(f"{Fore.CYAN}{'═' * 80}{Style.RESET_ALL}\n")

    checks = []

    # 1. Exchange connection
    try:
        from signalbolt.exchange.client import get_exchange

        exchange = get_exchange()
        ticker = exchange.get_ticker("BTCUSDT")
        btc_price = ticker.last_price if ticker else 0
        checks.append(("Exchange Connection", True, f"BTC: ${btc_price:,.2f}"))
    except Exception as e:
        checks.append(("Exchange Connection", False, str(e)))

    # 2. Configuration
    try:
        from signalbolt.core.config import get_config, list_configs

        config = get_config()
        configs = list_configs()
        checks.append(("Configuration", True, f"{len(configs)} configs available"))
    except Exception as e:
        checks.append(("Configuration", False, str(e)))

    # 3. Data directory
    try:
        data_dir = Path("data/paper_sessions")
        data_dir.mkdir(parents=True, exist_ok=True)
        checks.append(("Data Directory", True, str(data_dir)))
    except Exception as e:
        checks.append(("Data Directory", False, str(e)))

    # 4. Market data cache
    try:
        market_dir = Path("market_data/parquet")
        if market_dir.exists():
            files = list(market_dir.glob("*.parquet"))
            checks.append(("Market Data Cache", True, f"{len(files)} files"))
        else:
            checks.append(
                ("Market Data Cache", True, "Empty (will download on demand)")
            )
    except Exception as e:
        checks.append(("Market Data Cache", False, str(e)))

    # 5. API Keys
    try:
        from dotenv import load_dotenv

        load_dotenv()
        api_key = os.getenv("BINANCE_API_KEY", "")
        if api_key and len(api_key) > 10:
            checks.append(("API Keys", True, f"Configured ({len(api_key)} chars)"))
        else:
            checks.append(("API Keys", False, "Not configured in .env"))
    except Exception as e:
        checks.append(("API Keys", False, str(e)))

    # 6. Market Data Cache Status
    if _market_cache["last_update"]:
        age = (datetime.now() - _market_cache["last_update"]).total_seconds()
        checks.append(
            (
                "Market Data Cache",
                True,
                f"Age: {age:.0f}s (max: {_market_cache['cache_duration']}s)",
            )
        )
    else:
        checks.append(("Market Data Cache", True, "Not loaded yet"))

    # Print results
    for name, ok, detail in checks:
        emoji = "✅" if ok else "❌"
        status = (
            f"{Fore.GREEN}OK{Style.RESET_ALL}"
            if ok
            else f"{Fore.RED}FAIL{Style.RESET_ALL}"
        )
        print(f"  {emoji} {name:<25} {status}")
        print(f"     {Fore.LIGHTBLACK_EX}{detail}{Style.RESET_ALL}")
        print()

    # System info
    print(f"{Fore.CYAN}{'─' * 80}{Style.RESET_ALL}")
    print(f"\n  📦 Version: 1.0.0")
    print(f"  🐍 Python: {sys.version.split()[0]}")

    import platform

    print(f"  💻 Platform: {platform.system()} {platform.release()}")

    print()

    input(f"{Fore.CYAN}Press Enter to continue...{Style.RESET_ALL}")


def show_about_info():
    """Show About & Info menu."""

    def _run_about():
        from signalbolt.cli.about_menu import run_about_menu

        run_about_menu()

    try:
        run_with_interrupt_handler(_run_about)
    except Exception as e:
        print(f"\n{Fore.RED}Error: {e}{Style.RESET_ALL}\n")
        import traceback

        traceback.print_exc()


def show_help():
    """Show help and documentation."""
    help_text = f"""
{Fore.CYAN}╔{"═" * 78}╗
║{Fore.WHITE}{"SIGNALBOLT - QUICK START GUIDE":^78}{Fore.CYAN}║
╚{"═" * 78}╝

{Fore.WHITE}1. FIRST TIME SETUP{Style.RESET_ALL}
   {Fore.GREEN}🎉 No API keys required for most features!{Style.RESET_ALL}
   
   SignalBolt uses {Fore.CYAN}public market data{Style.RESET_ALL} from Binance.
   You can start immediately without any account or API keys.
   
   {Fore.GREEN}✓ Backtest{Style.RESET_ALL}        - No keys needed
   {Fore.GREEN}✓ Paper Trading{Style.RESET_ALL}   - No keys needed  
   {Fore.GREEN}✓ Signals Only{Style.RESET_ALL}    - No keys needed
   {Fore.GREEN}✓ Coin Details{Style.RESET_ALL}    - No keys needed
   
   {Fore.RED}✗ Live Trading{Style.RESET_ALL}    - Requires API keys (spot trading enabled)
   
   {Fore.YELLOW}Optional: Telegram/Discord Alerts{Style.RESET_ALL}
   • Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env
   • Add DISCORD_WEBHOOK_URL to .env

{Fore.WHITE}2. RECOMMENDED WORKFLOW{Style.RESET_ALL}
   {Fore.CYAN}Step 1:{Style.RESET_ALL} Run {Fore.GREEN}Backtest{Style.RESET_ALL} to understand the strategy
            → Test on historical data (weeks/months in seconds)
            → No setup required - just start!
   
   {Fore.CYAN}Step 2:{Style.RESET_ALL} Analyze {Fore.GREEN}Coin Details{Style.RESET_ALL} for specific symbols
            → Deep dive into indicators and signals
            → See real-time market analysis
   
   {Fore.CYAN}Step 3:{Style.RESET_ALL} Start {Fore.GREEN}Paper Trading{Style.RESET_ALL} for 30-60 days
            → Virtual money, real market conditions
            → Build confidence without any risk
            → Track performance over time
   
   {Fore.CYAN}Step 4:{Style.RESET_ALL} Enable {Fore.GREEN}Signals Only{Style.RESET_ALL} with alerts
            → Get notified via Telegram/Discord
            → Observe signals before trusting them
   
   {Fore.CYAN}Step 5:{Style.RESET_ALL} Analyze results thoroughly
            → Check win rate, drawdown, consistency
            → Compare with backtest expectations
            → Understand when strategy works (and when it doesn't)
   
   {Fore.CYAN}Step 6:{Style.RESET_ALL} {Fore.YELLOW}(Optional){Style.RESET_ALL} Consider {Fore.RED}Live Trading{Style.RESET_ALL}
            → {Fore.RED}Only if Paper Trading was consistently profitable{Style.RESET_ALL}
            → Start with minimal capital you can afford to lose
            → Requires Binance API keys with spot trading enabled

{Fore.WHITE}3. CONFIGURATIONS{Style.RESET_ALL}
   • {Fore.GREEN}config_safe.yaml{Style.RESET_ALL}       - Conservative (bear market) - {Fore.GREEN}RECOMMENDED{Style.RESET_ALL}
   • {Fore.YELLOW}config_balanced.yaml{Style.RESET_ALL}  - Balanced (range market)
   • {Fore.RED}config_aggressive.yaml{Style.RESET_ALL} - Aggressive (bull market) - {Fore.RED}HIGH RISK{Style.RESET_ALL}
   
   Edit configs in: {Fore.CYAN}configs/paper/{Style.RESET_ALL} or {Fore.CYAN}configs/live/{Style.RESET_ALL}
   Full reference:  {Fore.CYAN}configs/_template.yaml{Style.RESET_ALL}

{Fore.WHITE}4. LIVE TRADING SETUP (only if needed){Style.RESET_ALL}
   {Fore.RED}⚠️  Live Trading uses REAL money. You can lose everything.{Style.RESET_ALL}
   
   {Fore.CYAN}If you still want to proceed:{Style.RESET_ALL}
   1. Create API key on Binance
   2. Enable: {Fore.YELLOW}✓ Reading{Style.RESET_ALL}, {Fore.YELLOW}✓ Spot Trading{Style.RESET_ALL}
   3. Disable: {Fore.RED}✗ Withdrawal (CRITICAL!){Style.RESET_ALL}, {Fore.RED}✗ Futures{Style.RESET_ALL}, {Fore.RED}✗ Margin{Style.RESET_ALL}
   4. Copy keys to .env file:
      BINANCE_API_KEY=your_key
      BINANCE_API_SECRET=your_secret
   5. Restrict to your IP address (recommended)

{Fore.WHITE}5. MONITORING{Style.RESET_ALL}
   • Paper Trading runs 24/7 with virtual $1000 balance
   • Monitor via interactive CLI interface
   • Check positions and P&L in real-time
   • Market regime is auto-detected (Bull/Bear/Range/Crash)
   • Enable Telegram/Discord for mobile notifications

{Fore.WHITE}6. SAFETY TIPS{Style.RESET_ALL}
   • {Fore.GREEN}Start with Backtest and Paper Trading{Style.RESET_ALL} - zero risk!
   • Never invest more than you can afford to lose
   • Understand the strategy before using real money
   • Markets are unpredictable - no guarantees
   • Past performance ≠ future results
   • This is experimental software - use at your own risk
   • {Fore.RED}For Live: NEVER enable withdrawal on API keys{Style.RESET_ALL}

{Fore.WHITE}7. KEYBOARD SHORTCUTS{Style.RESET_ALL}
   • {Fore.YELLOW}Ctrl+C{Style.RESET_ALL} in submenu → Returns to previous menu
   • {Fore.YELLOW}Ctrl+C{Style.RESET_ALL} in main menu → Asks for exit confirmation
   • {Fore.YELLOW}Ctrl+C{Style.RESET_ALL} during scan → Pauses safely

{Fore.WHITE}8. COMMAND LINE OPTIONS{Style.RESET_ALL}
   {Fore.CYAN}python run.py{Style.RESET_ALL}                    Interactive menu (default)
   {Fore.CYAN}python run.py --type backtest{Style.RESET_ALL}    Start backtest directly
   {Fore.CYAN}python run.py --type paper{Style.RESET_ALL}       Start paper trading
   {Fore.CYAN}python run.py --type signals{Style.RESET_ALL}     Start signals only
   {Fore.CYAN}python run.py --quick-start{Style.RESET_ALL}      Skip intro screens

{Fore.WHITE}9. SUPPORT & DOCUMENTATION{Style.RESET_ALL}
   • {Fore.CYAN}README.md{Style.RESET_ALL}              - Getting started guide
   • {Fore.CYAN}CHANGELOG.md{Style.RESET_ALL}           - Version history  
   • {Fore.CYAN}DISCLAIMER.md{Style.RESET_ALL}          - Important risk information
   • {Fore.CYAN}configs/_template.yaml{Style.RESET_ALL} - Full configuration reference
   • {Fore.CYAN}GitHub Issues{Style.RESET_ALL}          - Report bugs or request features
   • {Fore.CYAN}About & Info{Style.RESET_ALL}           - Version, changelog, credits

{Fore.GREEN}💡 TIP: You can start Paper Trading RIGHT NOW without any setup!
   Just select [1] Paper Trading from the main menu.{Style.RESET_ALL}

{Fore.YELLOW}⚠️  IMPORTANT: This is experimental software. Trading is risky.
   Read DISCLAIMER.md before considering real money.{Style.RESET_ALL}

"""
    clear_screen()
    print(help_text)
    input(f"{Fore.CYAN}Press Enter to return to menu...{Style.RESET_ALL}")


import argparse


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="SignalBolt - Crypto Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run.py                     # Interactive menu (default)
  python run.py --type paper        # Start paper trading directly
  python run.py --type backtest     # Start backtest menu
  python run.py --type signals      # Start signals-only mode
  python run.py --quick-start       # Skip warnings, go straight to menu
        """,
    )

    parser.add_argument(
        "--type",
        choices=["paper", "live", "signals", "backtest", "coin"],
        help="Start specific mode directly (skips main menu)",
    )

    parser.add_argument(
        "--config", type=str, help="Config file to use (e.g., config_safe.yaml)"
    )

    parser.add_argument(
        "--quick-start",
        action="store_true",
        help="Skip banner and warnings (for automation)",
    )

    parser.add_argument(
        "--no-cache", action="store_true", help="Disable market data cache"
    )

    parser.add_argument("--version", action="version", version="SignalBolt v1.0.0")

    return parser.parse_args()


# =============================================================================
# UTILITIES
# =============================================================================


def clear_screen():
    """Clear terminal screen."""
    os.system("cls" if os.name == "nt" else "clear")


# =============================================================================
# MAIN LOOP
# =============================================================================


def main():
    """Main application loop."""

    # PARSE ARGUMENTS FIRST
    args = parse_arguments()

    # Pre-flight checks
    check_dependencies()
    ensure_directories()

    # Quick start mode (skip banner/warning)
    if not args.quick_start:
        clear_screen()
        show_banner()
        show_warning()

    # Initial market data fetch
    clear_screen()
    if not args.quick_start:
        show_banner()

    if not args.no_cache:
        fetch_market_data_with_message()
        time.sleep(0.5)

    #  DIRECT MODE (if --type specified)
    if args.type:
        if args.type == "paper":
            start_paper_trading()
        elif args.type == "signals":
            start_signals_only()
        elif args.type == "backtest":
            run_backtest()
        elif args.type == "coin":
            analyze_coin()
        elif args.type == "live":
            live_menu()

        # Exit after direct mode
        sys.exit(0)

    # Main loop (interactive menu)
    while True:
        try:
            clear_screen()
            show_status_bar()
            show_main_menu()

            choice = input(
                f"{Fore.YELLOW}Enter your choice [0-9]: {Style.RESET_ALL}"
            ).strip()

            if choice == "1":
                live_menu()

            elif choice == "2":
                start_paper_trading()

            elif choice == "3":
                start_signals_only()

            elif choice == "4":
                run_backtest()

            elif choice == "5":
                analyze_coin()

            elif choice == "6":
                manage_config()

            elif choice == "7":
                show_system_status()

            elif choice == "8":
                show_help()

            elif choice == "9":
                show_about_info()

            elif choice == "0":
                clear_screen()
                print(f"\n{Fore.CYAN}")
                print(
                    rf"   ,-,--.   .=-.-.    _,---.  .-._         ,---.                              _,.---._           ,--.--------.  "
                )
                print(
                    rf" ,-.'-  _\ /==/_ /_.='.'-,  \/==/ \  .-._.--.'  \       _.-.       _..---.  ,-.' , -  `.    _.-./==/,  -   , -\ "
                )
                print(
                    rf"/==/_ ,_.'|==|, |/==.'-     /|==|, \/ /, |==\-/\ \    .-,.'|     .' .'.-. \/==/_,  ,  - \ .-,.'|\==\.-.  - ,-./ "
                )
                print(
                    rf"\==\  \   |==|  /==/ -   .-' |==|-  \|  |/==/-|_\ |  |==|, |    /==/- '=' /==|   .=.     |==|, | `--`\==\- \    "
                )
                print(
                    rf" \==\ -\  |==|- |==|_   /_,-.|==| ,  | -|\==\,   - \ |==|- |    |==|-,   '|==|_ : ;=:  - |==|- |      \==\_ \   "
                )
                print(
                    rf" _\==\ ,\ |==| ,|==|  , \_.' )==| -   _ |/==/ -   ,| |==|, |    |==|  .=. \==| , '='     |==|, |      |==|- |   "
                )
                print(
                    rf"/==/\/ _ ||==|- \==\-  ,    (|==|  /\ , /==/-  /\ - \|==|- `-._ /==/- '=' ,\==\ -    ,_ /|==|- `-._   |==|, |   "
                )
                print(
                    rf"\==\ - , //==/. //==/ _  ,  //==/, | |- \==\ _.\=\.-'/==/ - , ,/==|   -   / '.='. -   .' /==/ - , ,/  /==/ -/   "
                )
                print(
                    rf" `--`---' `--`-` `--`------' `--`./  `--``--`        `--`-----'`-._`.___,'    `--`--''   `--`-----'   `--`--`   "
                )
                print(f"{Style.RESET_ALL}")
                print(f"  {Fore.WHITE}Thank you for using SignalBolt!{Style.RESET_ALL}")
                print(
                    f"  {Fore.YELLOW}Trade safely and responsibly.{Style.RESET_ALL}\n"
                )
                sys.exit(0)

            else:
                print(f"\n{Fore.RED}Invalid choice. Please try again.{Style.RESET_ALL}")
                time.sleep(1)

        except KeyboardInterrupt:
            # In main menu, ask for confirmation
            print()
            if confirm_exit():
                clear_screen()
                print(f"\n{Fore.CYAN}")
                print(
                    rf"   ,-,--.   .=-.-.    _,---.  .-._         ,---.                              _,.---._           ,--.--------.  "
                )
                print(
                    rf" ,-.'-  _\ /==/_ /_.='.'-,  \/==/ \  .-._.--.'  \       _.-.       _..---.  ,-.' , -  `.    _.-./==/,  -   , -\ "
                )
                print(
                    rf"/==/_ ,_.'|==|, |/==.'-     /|==|, \/ /, |==\-/\ \    .-,.'|     .' .'.-. \/==/_,  ,  - \ .-,.'|\==\.-.  - ,-./ "
                )
                print(
                    rf"\==\  \   |==|  /==/ -   .-' |==|-  \|  |/==/-|_\ |  |==|, |    /==/- '=' /==|   .=.     |==|, | `--`\==\- \    "
                )
                print(
                    rf" \==\ -\  |==|- |==|_   /_,-.|==| ,  | -|\==\,   - \ |==|- |    |==|-,   '|==|_ : ;=:  - |==|- |      \==\_ \   "
                )
                print(
                    rf" _\==\ ,\ |==| ,|==|  , \_.' )==| -   _ |/==/ -   ,| |==|, |    |==|  .=. \==| , '='     |==|, |      |==|- |   "
                )
                print(
                    rf"/==/\/ _ ||==|- \==\-  ,    (|==|  /\ , /==/-  /\ - \|==|- `-._ /==/- '=' ,\==\ -    ,_ /|==|- `-._   |==|, |   "
                )
                print(
                    rf"\==\ - , //==/. //==/ _  ,  //==/, | |- \==\ _.\=\.-'/==/ - , ,/==|   -   / '.='. -   .' /==/ - , ,/  /==/ -/   "
                )
                print(
                    rf" `--`---' `--`-` `--`------' `--`./  `--``--`        `--`-----'`-._`.___,'    `--`--''   `--`-----'   `--`--`   "
                )
                print(f"{Style.RESET_ALL}")
                print(f"  {Fore.WHITE}Thank you for using SignalBolt!{Style.RESET_ALL}")
                print(
                    f"  {Fore.YELLOW}Trade safely and responsibly.{Style.RESET_ALL}\n"
                )
                sys.exit(0)
            # If not confirmed, continue loop


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{Fore.YELLOW}Interrupted by user. Exiting...{Style.RESET_ALL}\n")
        sys.exit(0)
    except Exception as e:
        print(f"\n{Fore.RED}Fatal error: {e}{Style.RESET_ALL}\n")
        import traceback

        traceback.print_exc()
        sys.exit(1)

        traceback.print_exc()
        sys.exit(1)
