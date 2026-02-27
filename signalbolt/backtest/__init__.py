"""
SignalBolt Backtest Module.

Core backtesting functionality with optional PRO features.
"""

from pathlib import Path

from signalbolt.backtest.engine import (
    BacktestEngine,
    BacktestConfig,
    BacktestResult,
    BacktestTrade,
    MarketRegime,
    RegimeConfig,
    RegimeDetector,
    RegimePresets,
    SignalScanner,
)

from signalbolt.backtest.data_manager import DataManager
from signalbolt.backtest.reporter import BacktestReporter

# =============================================================================
# HTML REPORTER (Available for ALL)
# =============================================================================

try:
    from signalbolt.backtest.html_reporter import (
        HTMLReporter,
        MultiConfigHTMLReporter,
        generate_html_report,
        generate_comparison_report,
        PLOTLY_AVAILABLE,
    )

    HTML_EXPORT_AVAILABLE = PLOTLY_AVAILABLE
except ImportError:
    HTMLReporter = None
    MultiConfigHTMLReporter = None
    generate_html_report = None
    generate_comparison_report = None
    HTML_EXPORT_AVAILABLE = False


# =============================================================================
# PRO FEATURES (Check if pro/ folder exists)
# =============================================================================

PRO_DIR = Path(__file__).parent / "pro"
PRO_AVAILABLE = PRO_DIR.exists() and (PRO_DIR / "__init__.py").exists()

# Individual feature flags
PRO_MONTE_CARLO = False
PRO_WALK_FORWARD = False
PRO_BENCHMARK = False
PRO_MULTI_SYMBOL = False
PRO_MULTI_CONFIG = False

# Try to import PRO features
if PRO_AVAILABLE:
    try:
        from signalbolt.backtest.pro import PRO_ENABLED

        if PRO_ENABLED:
            from signalbolt.backtest.pro.monte_carlo import (
                MonteCarloSimulator,
                MonteCarloResult,
            )

            PRO_MONTE_CARLO = True

            from signalbolt.backtest.pro.walk_forward import (
                WalkForwardAnalyzer,
                WalkForwardResult,
                WalkForwardWindow,
            )

            PRO_WALK_FORWARD = True

            from signalbolt.backtest.pro.benchmark_comparison import (
                BenchmarkComparator,
                BenchmarkResult,
            )

            PRO_BENCHMARK = True

            from signalbolt.backtest.pro.multi_symbol import (
                MultiSymbolTester,
                MultiSymbolResult,
            )

            PRO_MULTI_SYMBOL = True

            from signalbolt.backtest.pro.multi_config import (
                MultiConfigTester,
                MultiConfigResult,
                ConfigComparisonReport,
            )

            PRO_MULTI_CONFIG = True

    except ImportError as e:
        import logging

        logging.getLogger(__name__).debug(f"PRO import error: {e}")

# Set None for unavailable features
if not PRO_MONTE_CARLO:
    MonteCarloSimulator = None
    MonteCarloResult = None

if not PRO_WALK_FORWARD:
    WalkForwardAnalyzer = None
    WalkForwardResult = None
    WalkForwardWindow = None

if not PRO_BENCHMARK:
    BenchmarkComparator = None
    BenchmarkResult = None

if not PRO_MULTI_SYMBOL:
    MultiSymbolTester = None
    MultiSymbolResult = None

if not PRO_MULTI_CONFIG:
    MultiConfigTester = None
    MultiConfigResult = None
    ConfigComparisonReport = None


__all__ = [
    # Core
    "BacktestEngine",
    "BacktestConfig",
    "BacktestResult",
    "BacktestTrade",
    "DataManager",
    "BacktestReporter",
    # Regime
    "MarketRegime",
    "RegimeConfig",
    "RegimeDetector",
    "RegimePresets",
    "SignalScanner",
    # HTML (All users)
    "HTMLReporter",
    "MultiConfigHTMLReporter",
    "generate_html_report",
    "generate_comparison_report",
    "HTML_EXPORT_AVAILABLE",
    # PRO Features
    "PRO_AVAILABLE",
    "PRO_MONTE_CARLO",
    "PRO_WALK_FORWARD",
    "PRO_BENCHMARK",
    "PRO_MULTI_SYMBOL",
    "PRO_MULTI_CONFIG",
    # PRO Classes
    "MonteCarloSimulator",
    "MonteCarloResult",
    "WalkForwardAnalyzer",
    "WalkForwardResult",
    "WalkForwardWindow",
    "BenchmarkComparator",
    "BenchmarkResult",
    "MultiSymbolTester",
    "MultiSymbolResult",
    "MultiConfigTester",
    "MultiConfigResult",
    "ConfigComparisonReport",
]
