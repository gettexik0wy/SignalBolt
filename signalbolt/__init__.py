"""
SignalBolt - Crypto Trading Bot

A regime-aware cryptocurrency trading bot with paper trading,
backtesting, and live trading capabilities.
"""

from dotenv import load_dotenv
import os
from pathlib import Path

# ============================================================================
# LOAD .ENV ON PACKAGE IMPORT
# ============================================================================

# Find project root: signalbolt/ -> SignalBolt/
_package_dir = Path(__file__).parent  # SignalBolt/signalbolt/
_project_root = _package_dir.parent  # SignalBolt/
_env_path = _project_root / ".env"

# Load .env if exists
if _env_path.exists():
    load_dotenv(_env_path, override=True)
    _env_loaded = True
else:
    load_dotenv()  # Try to find .env in parent directories
    _env_loaded = False

# ============================================================================
# METADATA
# ============================================================================

__version__ = "1.0.0"
__author__ = "gettexik"

# ============================================================================
# DEBUG MODE
# ============================================================================

DEBUG = os.getenv("SIGNALBOLT_DEBUG", "false").lower() == "true"

if DEBUG:
    print("=" * 60)
    print("  SIGNALBOLT PACKAGE LOADED")
    print("=" * 60)
    print(f"  Version:      {__version__}")
    print(f"  Package:      {_package_dir}")
    print(f"  Project Root: {_project_root}")
    print(f"  .env loaded:  {'✓ ' + str(_env_path) if _env_loaded else '✗ not found'}")
    print("=" * 60 + "\n")

# ============================================================================
# IMPORTS
# ============================================================================

from signalbolt.core.config import Config, get_config
from signalbolt.utils.logger import get_logger

__all__ = [
    "Config",
    "get_config",
    "get_logger",
    "__version__",
    "__author__",
]
