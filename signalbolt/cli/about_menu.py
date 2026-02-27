"""
About & Info Menu for SignalBolt.

Displays:
- Project information
- Changelog / Version history
- Credits & Author
- Dependencies
- License
- System information

Usage:
    from signalbolt.cli.about_menu import run_about_menu
    run_about_menu()
"""

import os
import sys
import platform
from datetime import datetime
from typing import Optional, List, Tuple
from pathlib import Path

try:
    from colorama import Fore, Style, init

    init(autoreset=True)
except ImportError:
    # Fallback if colorama not installed
    class Fore:
        RED = GREEN = YELLOW = CYAN = WHITE = MAGENTA = BLUE = ""
        LIGHTBLACK_EX = LIGHTRED_EX = LIGHTGREEN_EX = LIGHTYELLOW_EX = ""
        LIGHTBLUE_EX = LIGHTMAGENTA_EX = LIGHTCYAN_EX = LIGHTWHITE_EX = ""

    class Style:
        RESET_ALL = BRIGHT = DIM = ""


# =============================================================================
# VERSION INFO
# =============================================================================

VERSION = "1.0.0"
VERSION_DATE = "2026-02-28"
VERSION_CODENAME = "Genesis"

AUTHOR = "gettexik"
AUTHOR_GITHUB = "https://github.com/gettexik0wy"
PROJECT_URL = "https://github.com/gettexik0wy/signalbolt"
DOCS_URL = "I'm broke, so I don't have website ;("
DISCORD_URL = "https://discord.gg/JWeKseJsmE"

LICENSE = "MIT"


# =============================================================================
# ASCII ART
# =============================================================================

LOGO_LARGE = f"""
{Fore.RED}
   ,-,--.   .=-.-.    _,---.  .-._         ,---.                              _,.---._           ,--.--------.  
 ,-.'-  _\\ /==/_ /_.='.'-,  \\/==/ \\  .-._.--.'  \\       _.-.       _..---.  ,-.' , -  `.    _.-./==/,  -   , -\\ 
/==/_ ,_.'|==|, |/==.'-     /|==|, \\/ /, |==\\-/\\ \\    .-,.'|     .' .'.-. \\/==/_,  ,  - \\ .-,.'|\\==\\.-.  - ,-./ 
\\==\\  \\   |==|  /==/ -   .-' |==|-  \\|  |/==/-|_\\ |  |==|, |    /==/- '=' /==|   .=.     |==|, | `--`\\==\\- \\    
 \\==\\ -\\  |==|- |==|_   /_,-.|==| ,  | -|\\==\\,   - \\ |==|- |    |==|-,   '|==|_ : ;=:  - |==|- |      \\==\\_ \\   
 _\\==\\ ,\\ |==| ,|==|  , \\_.' )==| -   _ |/==/ -   ,| |==|, |    |==|  .=. \\==| , '='     |==|, |      |==|- |   
/==/\\/ _ ||==|- \\==\\-  ,    (|==|  /\\ , /==/-  /\\ - \\|==|- `-._ /==/- '=' ,\\==\\ -    ,_ /|==|- `-._   |==|, |   
\\==\\ - , //==/. //==/ _  ,  //==/, | |- \\==\\ _.\=\\.-'/==/ - , ,/==|   -   / '.='. -   .' /==/ - , ,/  /==/ -/   
 `--`---' `--`-` `--`------' `--`./  `--``--`        `--`-----'`-._`.___,'    `--`--''   `--`-----'   `--`--`   
{Style.RESET_ALL}
{Fore.LIGHTRED_EX}                              ⚡ Regime-Aware Crypto Trading Bot ⚡{Style.RESET_ALL}
"""

BOLT_ASCII = f"""
{Fore.YELLOW}
        ⚡⚡⚡
       ⚡⚡⚡⚡⚡
      ⚡⚡⚡⚡⚡⚡⚡
     ⚡⚡⚡    ⚡⚡⚡
    ⚡⚡⚡      ⚡⚡⚡
   ⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡
  ⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡
       ⚡⚡⚡⚡⚡
        ⚡⚡⚡
         ⚡
{Style.RESET_ALL}
"""


# =============================================================================
# CHANGELOG DATA
# =============================================================================

CHANGELOG = [
    {
        "version": "1.0.0",
        "date": "2026-02-28",
        "codename": "Genesis",
        "type": "major",
        "changes": [
            "🎉 Initial public release",
            "📊 SignalBolt Original Strategy with EMA alignment",
            "🔄 Multi-Timeframe (MTF) support (1m to 1M)",
            "🌍 Regime Detection (Bull/Bear/Range/Crash)",
            "📈 Paper Trading with virtual portfolio",
            "🔔 Signals-Only mode with Telegram/Discord alerts",
            "⏮️ Backtesting engine with HTML reports",
            "📉 Monte Carlo simulation & Walk-Forward analysis",
            "⚙️ Hot-reload configuration",
            "🎨 Interactive CLI with colored menus",
        ],
    },
]

COMING_SOON = [
    "🔮 Machine Learning signal enhancement",
    "🟢 Live Trading"
    "📜 Your suggestions for changes"
    "📰 News sentiment analysis",
    "📊 Order book imbalance detection",
    "🌐 Web dashboard",
    "🔗 Multi-exchange support (Bybit, OKX etc.)",
    "💎 Futures/Margin trading",
    "🤖 Auto-optimization of parameters",
]


# =============================================================================
# CREDITS DATA
# =============================================================================

CREDITS = {
    "core_team": [
        (
            "SignalBolt Team (A large team with only one person)",
            "Core Development",
            "🧠",
        ),
    ],
    "special_thanks": [
        ("Open Source Community", "Inspiration & Support"),
        ("Python Community", "Amazing ecosystem"),
        ("Binance", "Comprehensive API"),
        ("Me (gettexik)", "For the sleepless nights spent working on the project"),
    ],
}

LIBRARIES = [
    ("pandas", "Data manipulation & analysis", "https://pandas.pydata.org"),
    ("numpy", "Numerical computing", "https://numpy.org"),
    ("pandas-ta", "Technical indicators", "https://github.com/twopirllc/pandas-ta"),
    ("ccxt", "Cryptocurrency exchange library", "https://github.com/ccxt/ccxt"),
    ("requests", "HTTP library", "https://requests.readthedocs.io"),
    ("PyYAML", "YAML parser", "https://pyyaml.org"),
    ("colorama", "Colored terminal output", "https://github.com/tartley/colorama"),
    (
        "python-dotenv",
        "Environment variables",
        "https://github.com/theskumar/python-dotenv",
    ),
    ("plotly", "Interactive charts", "https://plotly.com/python/"),
]


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def clear_screen():
    """Clear terminal screen."""
    os.system("cls" if os.name == "nt" else "clear")


def print_header(title: str, width: int = 80):
    """Print a styled header."""
    print(f"\n{Fore.CYAN}╔{'═' * (width - 2)}╗")
    print(f"║{Fore.WHITE}{title:^{width - 2}}{Fore.CYAN}║")
    print(f"╚{'═' * (width - 2)}╝{Style.RESET_ALL}\n")


def print_separator(char: str = "─", width: int = 80):
    """Print a separator line."""
    print(f"{Fore.CYAN}{char * width}{Style.RESET_ALL}")


def wait_for_enter(message: str = "Press Enter to continue..."):
    """Wait for user to press Enter."""
    input(f"\n{Fore.YELLOW}{message}{Style.RESET_ALL}")


def print_menu_option(number: str, title: str, description: str = ""):
    """Print a menu option."""
    desc = (
        f"{Fore.LIGHTBLACK_EX}({description}){Style.RESET_ALL}" if description else ""
    )
    print(f"  {Fore.GREEN}[{number}]{Fore.WHITE} {title:<25} {desc}")


# =============================================================================
# MENU SCREENS
# =============================================================================


def show_about():
    """Display About SignalBolt screen."""
    clear_screen()
    print(LOGO_LARGE)
    print_header("ABOUT SIGNALBOLT")

    info = f"""
  {Fore.WHITE}SignalBolt{Style.RESET_ALL} is an open-source cryptocurrency trading bot
  designed for {Fore.GREEN}regime-aware{Style.RESET_ALL} automated trading.

  {Fore.CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Style.RESET_ALL}

  {Fore.YELLOW}Version:{Style.RESET_ALL}      {VERSION} "{VERSION_CODENAME}"
  {Fore.YELLOW}Released:{Style.RESET_ALL}     {VERSION_DATE}
  {Fore.YELLOW}License:{Style.RESET_ALL}      {LICENSE}
  {Fore.YELLOW}Author:{Style.RESET_ALL}       {AUTHOR}

  {Fore.CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Style.RESET_ALL}

  {Fore.WHITE}Key Features:{Style.RESET_ALL}

  {Fore.GREEN}•{Style.RESET_ALL} EMA Alignment Strategy with ADX & RSI confirmation
  {Fore.GREEN}•{Style.RESET_ALL} Multi-Timeframe (MTF) analysis (1m to 1 month)
  {Fore.GREEN}•{Style.RESET_ALL} Dynamic Regime Detection (Bull/Bear/Range/Crash)
  {Fore.GREEN}•{Style.RESET_ALL} Paper Trading for risk-free testing
  {Fore.GREEN}•{Style.RESET_ALL} Backtesting with Monte Carlo simulation
  {Fore.GREEN}•{Style.RESET_ALL} Telegram & Discord alerts
  {Fore.GREEN}•{Style.RESET_ALL} Hot-reload configuration

  {Fore.CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Style.RESET_ALL}

  {Fore.WHITE}Links:{Style.RESET_ALL}

  {Fore.YELLOW}📦 GitHub:{Style.RESET_ALL}    {Fore.LIGHTBLUE_EX}{PROJECT_URL}{Style.RESET_ALL}
  {Fore.YELLOW}📚 Docs:{Style.RESET_ALL}      {Fore.LIGHTBLUE_EX}{DOCS_URL}{Style.RESET_ALL}
  {Fore.YELLOW}💬 Discord:{Style.RESET_ALL}   {Fore.LIGHTBLUE_EX}{DISCORD_URL}{Style.RESET_ALL}

"""
    print(info)
    wait_for_enter()


def show_changelog():
    """Display Changelog screen."""
    clear_screen()
    print(LOGO_LARGE)
    print_header("CHANGELOG")

    for release in CHANGELOG:
        version = release["version"]
        date = release["date"]
        codename = release["codename"]
        release_type = release["type"]
        changes = release["changes"]

        # Type badge
        if release_type == "major":
            badge = f"{Fore.GREEN}[MAJOR]{Style.RESET_ALL}"
        elif release_type == "minor":
            badge = f"{Fore.YELLOW}[MINOR]{Style.RESET_ALL}"
        else:
            badge = f"{Fore.LIGHTBLACK_EX}[PATCH]{Style.RESET_ALL}"

        print(f"  {Fore.CYAN}{'─' * 70}{Style.RESET_ALL}")
        print(
            f'  {Fore.WHITE}v{version}{Style.RESET_ALL} {badge} - "{Fore.MAGENTA}{codename}{Style.RESET_ALL}"'
        )
        print(f"  {Fore.LIGHTBLACK_EX}Released: {date}{Style.RESET_ALL}")
        print()

        for change in changes:
            print(f"    {change}")

        print()

    # Coming Soon
    print(f"  {Fore.CYAN}{'─' * 70}{Style.RESET_ALL}")
    print(f"  {Fore.YELLOW}🔮 COMING SOON{Style.RESET_ALL}")
    print()

    for item in COMING_SOON:
        print(f"    {Fore.LIGHTBLACK_EX}{item}{Style.RESET_ALL}")

    print()
    wait_for_enter()


def show_credits():
    """Display Credits & Author screen."""
    clear_screen()
    print(BOLT_ASCII)
    print_header("CREDITS & AUTHOR")

    # Core Team
    if "core_team" in CREDITS and CREDITS["core_team"]:
        print(f"  {Fore.WHITE}👥 CORE TEAM{Style.RESET_ALL}")
        print(f"  {Fore.CYAN}{'─' * 50}{Style.RESET_ALL}")

        for name, role, emoji in CREDITS["core_team"]:
            print(f"    {emoji} {Fore.GREEN}{name}{Style.RESET_ALL}")
            print(f"       {Fore.LIGHTBLACK_EX}{role}{Style.RESET_ALL}")

        print()

    # Contributors
    if "contributors" in CREDITS and CREDITS["contributors"]:
        print(f"  {Fore.WHITE}🛠️ CONTRIBUTORS{Style.RESET_ALL}")
        print(f"  {Fore.CYAN}{'─' * 50}{Style.RESET_ALL}")

        for name, contribution, emoji in CREDITS["contributors"]:
            print(f"    {emoji} {Fore.YELLOW}{name}{Style.RESET_ALL} - {contribution}")

        print()

    # Special Thanks
    if "special_thanks" in CREDITS and CREDITS["special_thanks"]:
        print(f"  {Fore.WHITE}🙏 SPECIAL THANKS{Style.RESET_ALL}")
        print(f"  {Fore.CYAN}{'─' * 50}{Style.RESET_ALL}")

        for item in CREDITS["special_thanks"]:
            if isinstance(item, tuple) and len(item) >= 2:
                name, reason = item[0], item[1]
                print(f"    {Fore.MAGENTA}♥{Style.RESET_ALL} {name}")
                print(f"       {Fore.LIGHTBLACK_EX}{reason}{Style.RESET_ALL}")
            else:
                print(f"    {Fore.MAGENTA}♥{Style.RESET_ALL} {item}")

        print()

    # Contact
    print(f"  {Fore.WHITE}📬 CONTACT{Style.RESET_ALL}")
    print(f"  {Fore.CYAN}{'─' * 50}{Style.RESET_ALL}")
    print(f"    {Fore.YELLOW}GitHub:{Style.RESET_ALL}  {AUTHOR_GITHUB}")
    print(f"    {Fore.YELLOW}Discord:{Style.RESET_ALL} {DISCORD_URL}")

    print()
    wait_for_enter()


def show_dependencies():
    """Display Dependencies screen."""
    clear_screen()
    print(LOGO_LARGE)
    print_header("DEPENDENCIES")

    print(
        f"  {Fore.WHITE}SignalBolt is built with these amazing libraries:{Style.RESET_ALL}\n"
    )

    # Check installed versions
    for lib_name, description, url in LIBRARIES:
        try:
            # Try to import and get version
            if lib_name == "PyYAML":
                import yaml

                version = getattr(yaml, "__version__", "installed")
            elif lib_name == "pandas-ta":
                import pandas_ta

                version = getattr(pandas_ta, "version", "installed")
            else:
                module = __import__(lib_name.replace("-", "_"))
                version = getattr(module, "__version__", "installed")

            status = f"{Fore.GREEN}✓ {version}{Style.RESET_ALL}"
        except ImportError:
            status = f"{Fore.RED}✗ Not installed{Style.RESET_ALL}"
        except Exception:
            status = f"{Fore.YELLOW}? Unknown{Style.RESET_ALL}"

        print(
            f"  {Fore.CYAN}📦{Style.RESET_ALL} {Fore.WHITE}{lib_name:<15}{Style.RESET_ALL} {status}"
        )
        print(f"     {Fore.LIGHTBLACK_EX}{description}{Style.RESET_ALL}")
        print(f"     {Fore.LIGHTBLUE_EX}{url}{Style.RESET_ALL}")
        print()

    # Python info
    print_separator()
    print(f"\n  {Fore.WHITE}🐍 PYTHON ENVIRONMENT{Style.RESET_ALL}\n")
    print(f"     Version:    {Fore.GREEN}{platform.python_version()}{Style.RESET_ALL}")
    print(f"     Platform:   {platform.system()} {platform.release()}")
    print(f"     Executable: {sys.executable}")

    print()
    wait_for_enter()


def show_license():
    """Display License screen."""
    clear_screen()
    print(LOGO_LARGE)
    print_header("LICENSE")

    mit_license = f"""
  {Fore.WHITE}MIT License{Style.RESET_ALL}
  
  {Fore.LIGHTBLACK_EX}Copyright (c) 2026 SignalBolt{Style.RESET_ALL}

  Permission is hereby granted, free of charge, to any person obtaining a copy
  of this software and associated documentation files (the "Software"), to deal
  in the Software without restriction, including without limitation the rights
  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
  copies of the Software, and to permit persons to whom the Software is
  furnished to do so, subject to the following conditions:

  The above copyright notice and this permission notice shall be included in all
  copies or substantial portions of the Software.

  {Fore.YELLOW}THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
  SOFTWARE.{Style.RESET_ALL}

  {Fore.CYAN}{"─" * 70}{Style.RESET_ALL}

  {Fore.RED}⚠️  DISCLAIMER{Style.RESET_ALL}

  {Fore.WHITE}This software is for educational purposes only.{Style.RESET_ALL}
  
  Trading cryptocurrencies involves substantial risk of loss and is not 
  suitable for every investor. The valuation of cryptocurrencies may fluctuate, 
  and, as a result, you may lose more than your original investment.
  
  {Fore.YELLOW}DO NOT trade with money you cannot afford to lose.{Style.RESET_ALL}
  
  Past performance is not indicative of future results.
  
  The developers of SignalBolt are NOT responsible for any financial losses
  incurred while using this software.
"""
    print(mit_license)
    wait_for_enter()


def show_system_info():
    """Display System Info screen."""
    clear_screen()
    print(LOGO_LARGE)
    print_header("SYSTEM INFORMATION")

    # SignalBolt Info
    print(f"  {Fore.WHITE}📊 SIGNALBOLT{Style.RESET_ALL}")
    print(f"  {Fore.CYAN}{'─' * 50}{Style.RESET_ALL}")
    print(f"     Version:      {Fore.GREEN}{VERSION}{Style.RESET_ALL}")
    print(f"     Codename:     {VERSION_CODENAME}")
    print(f"     Release Date: {VERSION_DATE}")
    print()

    # Python Info
    print(f"  {Fore.WHITE}🐍 PYTHON{Style.RESET_ALL}")
    print(f"  {Fore.CYAN}{'─' * 50}{Style.RESET_ALL}")
    print(
        f"     Version:      {Fore.GREEN}{platform.python_version()}{Style.RESET_ALL}"
    )
    print(f"     Executable:   {sys.executable}")
    print(f"     Platform:     {platform.platform()}")
    print()

    # System Info
    print(f"  {Fore.WHITE}💻 SYSTEM{Style.RESET_ALL}")
    print(f"  {Fore.CYAN}{'─' * 50}{Style.RESET_ALL}")
    print(f"     OS:           {platform.system()} {platform.release()}")
    print(f"     Architecture: {platform.machine()}")
    print(f"     Processor:    {platform.processor() or 'Unknown'}")
    print()

    # Paths
    print(f"  {Fore.WHITE}📁 PATHS{Style.RESET_ALL}")
    print(f"  {Fore.CYAN}{'─' * 50}{Style.RESET_ALL}")
    print(f"     Working Dir:  {os.getcwd()}")
    print(f"     Script Dir:   {Path(__file__).parent}")
    print()

    # Environment
    print(f"  {Fore.WHITE}🔧 ENVIRONMENT{Style.RESET_ALL}")
    print(f"  {Fore.CYAN}{'─' * 50}{Style.RESET_ALL}")

    # Check API keys (masked)
    api_key = os.getenv("BINANCE_API_KEY", "")
    api_status = (
        f"{Fore.GREEN}✓ Configured{Style.RESET_ALL}"
        if api_key
        else f"{Fore.RED}✗ Not set{Style.RESET_ALL}"
    )
    print(f"     Binance API:  {api_status}")

    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    tg_status = (
        f"{Fore.GREEN}✓ Configured{Style.RESET_ALL}"
        if telegram_token
        else f"{Fore.YELLOW}○ Not set{Style.RESET_ALL}"
    )
    print(f"     Telegram:     {tg_status}")

    discord_webhook = os.getenv("DISCORD_WEBHOOK_URL", "")
    dc_status = (
        f"{Fore.GREEN}✓ Configured{Style.RESET_ALL}"
        if discord_webhook
        else f"{Fore.YELLOW}○ Not set{Style.RESET_ALL}"
    )
    print(f"     Discord:      {dc_status}")

    print()
    wait_for_enter()


def show_easter_egg():
    """Display a fun easter egg."""
    clear_screen()

    egg = f"""
{Fore.YELLOW}
    🥚 You found an Easter Egg! 🐣
    
{Fore.CYAN}
         /\\_/\\  
        ( o.o ) 
         > ^ <
        /|   |\\
       (_|   |_)
       
{Fore.WHITE}
    "In the world of crypto, the patient trader 
     who manages risk shall inherit the gains."
                                    - Satoshi probably
{Fore.GREEN}
    🚀 HODL and trade responsibly! 🌙
    
{Fore.MAGENTA}
    Secret Stats:
    • Lines of code: ~15,000+
    • Cups of coffee: ∞
    • Bugs squashed: Too many to count
    • Sleep lost: Yes
    
{Style.RESET_ALL}
"""
    print(egg)

    # Konami code hint
    print(
        f"  {Fore.LIGHTBLACK_EX}Hint: There might be more secrets... try 'bolt'{Style.RESET_ALL}"
    )
    print()
    wait_for_enter()


def show_bolt_art():
    """Another easter egg - full bolt ASCII art."""
    clear_screen()

    bolt = f"""
{Fore.YELLOW}

                                    ████
                                   ██████
                                  ████████
                                 ██████████
                                ████████████
                               ██████████████
                              ████████████████
                             ██████████████████
                            ████████      ████
                           ████████        ██
                          ████████
                         ████████
                        ████████
                       ████████████████████
                      ██████████████████████
                     ████████████████████████
                    ██████████████████████████
                           ████████████
                            ██████████
                             ████████
                              ██████
                               ████
                                ██
                                █

{Fore.CYAN}
              ███████╗██╗ ██████╗ ███╗   ██╗ █████╗ ██╗     
              ██╔════╝██║██╔════╝ ████╗  ██║██╔══██╗██║     
              ███████╗██║██║  ███╗██╔██╗ ██║███████║██║     
              ╚════██║██║██║   ██║██║╚██╗██║██╔══██║██║     
              ███████║██║╚██████╔╝██║ ╚████║██║  ██║███████╗
              ╚══════╝╚═╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝  ╚═╝╚══════╝
                                                            
                    ██████╗  ██████╗ ██╗  ████████╗          
                    ██╔══██╗██╔═══██╗██║  ╚══██╔══╝          
                    ██████╔╝██║   ██║██║     ██║             
                    ██╔══██╗██║   ██║██║     ██║             
                    ██████╔╝╚██████╔╝███████╗██║             
                    ╚═════╝  ╚═════╝ ╚══════╝╚═╝             

{Fore.GREEN}
                         ⚡ POWER TRADING ⚡
{Style.RESET_ALL}
"""
    print(bolt)
    wait_for_enter()


# =============================================================================
# MAIN MENU
# =============================================================================


def run_about_menu():
    """Run the About & Info menu."""

    while True:
        clear_screen()
        print(LOGO_LARGE)
        print_header("ABOUT & INFO")

        print_menu_option("1", "About SignalBolt", "Project info & description")
        print_menu_option("2", "Changelog", "Version history")
        print_menu_option("3", "Credits & Author", "Who made this")
        print_menu_option("4", "Dependencies", "Libraries used")
        print_menu_option("5", "License", "MIT License & Disclaimer")
        print_menu_option("6", "System Info", "Environment details")
        print()
        print_menu_option("0", "Back to Main Menu", "")

        print()
        print_separator("═")
        print()

        choice = (
            input(f"  {Fore.YELLOW}Enter your choice [0-6]: {Style.RESET_ALL}")
            .strip()
            .lower()
        )

        if choice == "1":
            show_about()

        elif choice == "2":
            show_changelog()

        elif choice == "3":
            show_credits()

        elif choice == "4":
            show_dependencies()

        elif choice == "5":
            show_license()

        elif choice == "6":
            show_system_info()

        elif choice == "0" or choice == "":
            break

        # Easter eggs
        elif choice == "egg" or choice == "easter":
            show_easter_egg()

        elif choice == "bolt" or choice == "⚡":
            show_bolt_art()

        else:
            print(f"\n  {Fore.RED}Invalid choice. Please try again.{Style.RESET_ALL}")
            import time

            time.sleep(1)


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    try:
        run_about_menu()
    except KeyboardInterrupt:
        print(f"\n\n{Fore.YELLOW}Returning to main menu...{Style.RESET_ALL}")
