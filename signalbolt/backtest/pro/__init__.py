"""
SignalBolt PRO Backtest Features.

These modules require PRO license.
If this folder doesn't exist, PRO features are disabled.
"""

PRO_ENABLED = True  # This file exists = PRO enabled

from signalbolt.backtest.pro.monte_carlo import (
    MonteCarloSimulator,
    MonteCarloResult,
)

from signalbolt.backtest.pro.walk_forward import (
    WalkForwardAnalyzer,
    WalkForwardResult,
    WalkForwardWindow,
)

from signalbolt.backtest.pro.benchmark_comparison import (
    BenchmarkComparator,
    BenchmarkResult,
)

from signalbolt.backtest.pro.multi_symbol import (
    MultiSymbolTester,
    MultiSymbolResult,
)

from signalbolt.backtest.pro.multi_config import (
    MultiConfigTester,
    MultiConfigResult,
    ConfigComparisonReport,
)

__all__ = [
    "PRO_ENABLED",
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
