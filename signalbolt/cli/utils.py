"""
CLI utility functions with Colorama.

Colors, formatting, input helpers, progress bars, tables.
"""

import sys
import os
from typing import Optional, List, Any
from colorama import Fore, Back, Style, init

# Initialize colorama for Windows compatibility
init(autoreset=True)


# =============================================================================
# COLORS
# =============================================================================


def green(text: str) -> str:
    """Green text."""
    return f"{Fore.GREEN}{text}{Style.RESET_ALL}"


def red(text: str) -> str:
    """Red text."""
    return f"{Fore.RED}{text}{Style.RESET_ALL}"


def yellow(text: str) -> str:
    """Yellow text."""
    return f"{Fore.YELLOW}{text}{Style.RESET_ALL}"


def blue(text: str) -> str:
    """Blue text."""
    return f"{Fore.BLUE}{text}{Style.RESET_ALL}"


def cyan(text: str) -> str:
    """Cyan text."""
    return f"{Fore.CYAN}{text}{Style.RESET_ALL}"


def magenta(text: str) -> str:
    """Magenta text."""
    return f"{Fore.MAGENTA}{text}{Style.RESET_ALL}"


def white(text: str) -> str:
    """White text."""
    return f"{Fore.WHITE}{text}{Style.RESET_ALL}"


def bold(text: str) -> str:
    """Bold text."""
    return f"{Style.BRIGHT}{text}{Style.RESET_ALL}"


def dim(text: str) -> str:
    """Dim text."""
    return f"{Style.DIM}{text}{Style.RESET_ALL}"


def bold_green(text: str) -> str:
    """Bold green text."""
    return f"{Style.BRIGHT}{Fore.GREEN}{text}{Style.RESET_ALL}"


def bold_red(text: str) -> str:
    """Bold red text."""
    return f"{Style.BRIGHT}{Fore.RED}{text}{Style.RESET_ALL}"


def bold_yellow(text: str) -> str:
    """Bold yellow text."""
    return f"{Style.BRIGHT}{Fore.YELLOW}{text}{Style.RESET_ALL}"


def bold_cyan(text: str) -> str:
    """Bold cyan text."""
    return f"{Style.BRIGHT}{Fore.CYAN}{text}{Style.RESET_ALL}"


# =============================================================================
# FORMATTING
# =============================================================================


def format_usd(value: float, decimals: int = 2) -> str:
    """Format value as USD."""
    if abs(value) >= 1_000_000_000:
        return f"${value / 1_000_000_000:,.{decimals}f}B"
    elif abs(value) >= 1_000_000:
        return f"${value / 1_000_000:,.{decimals}f}M"
    elif abs(value) >= 1_000:
        return f"${value:,.{decimals}f}"
    else:
        return f"${value:.{decimals}f}"


def format_pct(value: float, decimals: int = 2, with_sign: bool = True) -> str:
    """Format value as percentage."""
    if with_sign:
        return f"{value:+.{decimals}f}%"
    return f"{value:.{decimals}f}%"


def format_pct_colored(value: float, decimals: int = 2) -> str:
    """Format percentage with color (green positive, red negative)."""
    formatted = f"{value:+.{decimals}f}%"
    if value > 0:
        return green(formatted)
    elif value < 0:
        return red(formatted)
    return formatted


def format_crypto_price(value: float) -> str:
    """Format crypto price (auto decimals based on magnitude)."""
    if value >= 1000:
        return f"{value:,.2f}"
    elif value >= 1:
        return f"{value:.4f}"
    elif value >= 0.001:
        return f"{value:.6f}"
    else:
        return f"{value:.8f}"


def format_duration(seconds: float) -> str:
    """Format seconds as human readable duration."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f}m"
    elif seconds < 86400:
        hours = seconds / 3600
        return f"{hours:.1f}h"
    else:
        days = seconds / 86400
        return f"{days:.1f}d"


def format_number(value: float, decimals: int = 2) -> str:
    """Format number with thousand separators."""
    return f"{value:,.{decimals}f}"


# =============================================================================
# TERMINAL HELPERS
# =============================================================================


def clear_screen():
    """Clear terminal screen."""
    os.system("cls" if os.name == "nt" else "clear")


def get_terminal_size() -> tuple:
    """Get terminal size (columns, rows)."""
    try:
        size = os.get_terminal_size()
        return size.columns, size.lines
    except:
        return 80, 24


def print_header(title: str, char: str = "*", width: int = 80):
    """Print formatted header."""
    line = char * width
    print(f"\n{line}")
    print(f"{title.center(width)}")
    print(f"{line}")


def print_divider(char: str = "-", width: int = 70):
    """Print divider line."""
    print(char * width)


def print_box(title: str, content: List[str], width: int = 70):
    """Print content in a box."""
    print(f"\n{'═' * width}")
    print(f"  {bold(title)}")
    print(f"{'─' * width}")
    for line in content:
        print(f"  {line}")
    print(f"{'═' * width}")


# =============================================================================
# INPUT HELPERS
# =============================================================================


def input_string(
    prompt: str, default: Optional[str] = None, min_length: int = 0
) -> str:
    """Get string input with optional default."""
    while True:
        if default:
            user_input = input(f"{prompt} (default: {default}): ").strip()
            if not user_input:
                return default
        else:
            user_input = input(f"{prompt}: ").strip()

        if len(user_input) >= min_length:
            return user_input

        if min_length > 0:
            print(red(f"Input must be at least {min_length} characters"))


def input_yes_no(prompt: str, default: Optional[bool] = None) -> bool:
    """Get yes/no input."""
    if default is True:
        prompt_suffix = "[Y/n]"
    elif default is False:
        prompt_suffix = "[y/N]"
    else:
        prompt_suffix = "[y/n]"

    while True:
        user_input = input(f"{prompt} {prompt_suffix}: ").strip().lower()

        if not user_input:
            if default is not None:
                return default
            continue

        if user_input in ("y", "yes"):
            return True
        elif user_input in ("n", "no"):
            return False

        print(red("Please enter 'y' or 'n'"))


def input_number(
    prompt: str,
    min_val: Optional[float] = None,
    max_val: Optional[float] = None,
    default: Optional[float] = None,
) -> float:
    """Get numeric input."""
    while True:
        default_str = f" (default: {default})" if default is not None else ""
        user_input = input(f"{prompt}{default_str}: ").strip()

        if not user_input:
            if default is not None:
                return default
            continue

        try:
            value = float(user_input)

            if min_val is not None and value < min_val:
                print(red(f"Value must be >= {min_val}"))
                continue

            if max_val is not None and value > max_val:
                print(red(f"Value must be <= {max_val}"))
                continue

            return value

        except ValueError:
            print(red("Please enter a valid number"))


def input_choice(prompt: str, choices: List[str], default: Optional[str] = None) -> str:
    """Get user input from list of choices."""
    choices_lower = [c.lower() for c in choices]

    while True:
        if default:
            user_input = input(
                f"{prompt} [{'/'.join(choices)}] (default: {default}): "
            ).strip()
            if not user_input:
                return default
        else:
            user_input = input(f"{prompt} [{'/'.join(choices)}]: ").strip()

        if user_input.lower() in choices_lower:
            return user_input.lower()

        print(red(f"Invalid choice. Choose from: {', '.join(choices)}"))


# =============================================================================
# MENU HELPERS
# =============================================================================


def print_menu(title: str, options: List[tuple], show_back: bool = True):
    """Print menu with numbered options."""
    print_header(title)
    print()

    for i, (key, label) in enumerate(options, 1):
        print(f"  {cyan(str(i))}. {label}")

    if show_back:
        print()
        print(f"  {dim('0. Back')}")

    print()


def get_menu_choice(options: List[tuple], show_back: bool = True) -> Optional[str]:
    """Get menu choice from user."""
    while True:
        user_input = input(f"{bold('Select option')}: ").strip()

        if not user_input:
            continue

        if show_back and user_input == "0":
            return None

        try:
            index = int(user_input)
            if 1 <= index <= len(options):
                return options[index - 1][0]
            print(red(f"Enter a number between 1 and {len(options)}"))
        except ValueError:
            for key, label in options:
                if user_input.lower() == key.lower():
                    return key
            print(red("Invalid option"))


# =============================================================================
# TABLE
# =============================================================================


def print_table(
    headers: List[str], rows: List[List[Any]], alignments: Optional[List[str]] = None
):
    """Print formatted table."""
    if not rows:
        print("No data")
        return

    if alignments is None:
        alignments = ["l"] * len(headers)

    widths = [len(str(h)) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))

    def format_cell(value, width, align):
        s = str(value)
        if align == "r":
            return s.rjust(width)
        elif align == "c":
            return s.center(width)
        return s.ljust(width)

    sep = "-+-".join("-" * w for w in widths)
    header_line = " | ".join(
        format_cell(h, widths[i], alignments[i]) for i, h in enumerate(headers)
    )

    print(f" {header_line} ")
    print(f"-{sep}-")

    for row in rows:
        row_line = " | ".join(
            format_cell(cell, widths[i], alignments[i]) for i, cell in enumerate(row)
        )
        print(f" {row_line} ")


# =============================================================================
# EMOJI HELPERS
# =============================================================================


def status_emoji(status: str) -> str:
    """Get emoji for status."""
    mapping = {
        "running": "🟢",
        "paused": "🟡",
        "stopped": "🔴",
        "error": "❌",
        "success": "✅",
        "warning": "⚠️",
        "info": "ℹ️",
        "long": "📈",
        "short": "📉",
        "profit": "💰",
        "loss": "💸",
    }
    return mapping.get(status.lower(), "❓")


def pnl_emoji(pnl_pct: float) -> str:
    """Get emoji for P&L."""
    if pnl_pct >= 5:
        return "🚀"
    elif pnl_pct >= 2:
        return "💰"
    elif pnl_pct >= 0:
        return "🟢"
    elif pnl_pct >= -2:
        return "🔴"
    else:
        return "💸"
