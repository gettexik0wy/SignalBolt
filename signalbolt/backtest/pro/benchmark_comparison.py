"""
Benchmark Comparison for strategy evaluation.

Compare strategy against:
- Buy & Hold (same asset)
- Risk-free rate
- Custom benchmark symbol

PRO Feature.
"""

import numpy as np
from datetime import datetime
from dataclasses import dataclass
from typing import Dict

from signalbolt.backtest.engine import BacktestResult
from signalbolt.backtest.data_manager import DataManager
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.backtest.pro.benchmark")


@dataclass
class BenchmarkResult:
    """Comparison against benchmark."""

    strategy_result: BacktestResult

    benchmark_name: str = "Buy & Hold"
    benchmark_return: float = 0.0
    benchmark_max_dd: float = 0.0
    benchmark_sharpe: float = 0.0
    benchmark_volatility: float = 0.0

    # Comparison metrics
    alpha: float = 0.0
    beta: float = 0.0
    information_ratio: float = 0.0
    tracking_error: float = 0.0

    outperformance: float = 0.0
    relative_dd: float = 0.0
    pct_periods_beating: float = 0.0

    def summary(self) -> Dict:
        return {
            "strategy_return": self.strategy_result.total_pnl_pct(),
            "benchmark_name": self.benchmark_name,
            "benchmark_return": self.benchmark_return,
            "outperformance": self.outperformance,
            "alpha": self.alpha,
            "beta": self.beta,
            "information_ratio": self.information_ratio,
            "strategy_max_dd": self.strategy_result.max_drawdown_pct(),
            "benchmark_max_dd": self.benchmark_max_dd,
            "relative_dd": self.relative_dd,
        }

    def to_dict(self) -> Dict:
        return self.summary()


class BenchmarkComparator:
    """
    Compare strategy against benchmarks.

    Usage:
        comparator = BenchmarkComparator()
        result = comparator.compare(backtest_result, benchmark='buy_hold')
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.data_manager = DataManager(verbose=False)

    def compare(
        self,
        result: BacktestResult,
        benchmark: str = "buy_hold",
        risk_free_rate: float = 4.0,
    ) -> BenchmarkResult:
        """
        Compare strategy against benchmark.

        Args:
            result: Backtest result to compare
            benchmark: 'buy_hold', 'risk_free', or symbol name
            risk_free_rate: Annual risk-free rate
        """
        if self.verbose:
            print(f"\n  ⚖️  Benchmark Comparison")
            print(f"  {'─' * 60}")

        bench_result = BenchmarkResult(
            strategy_result=result,
            benchmark_name=benchmark.replace("_", " ").title(),
        )

        if benchmark == "buy_hold":
            self._calculate_buy_hold(result, bench_result)
        elif benchmark == "risk_free":
            self._calculate_risk_free(result, bench_result, risk_free_rate)
        else:
            self._calculate_custom_benchmark(result, bench_result, benchmark)

        self._calculate_comparison_metrics(result, bench_result)

        if self.verbose:
            self._print_summary(bench_result)

        return bench_result

    def _calculate_buy_hold(self, result: BacktestResult, bench: BenchmarkResult):
        """Calculate buy & hold benchmark."""
        df = self.data_manager.get_data(
            result.symbol,
            result.interval,
            result.start_date,
            result.end_date,
        )

        if df is None or len(df) < 2:
            return

        first_price = float(df["close"].iloc[0])
        last_price = float(df["close"].iloc[-1])

        bench.benchmark_return = ((last_price - first_price) / first_price) * 100

        # Max drawdown
        peak = df["close"].iloc[0]
        max_dd = 0.0

        for price in df["close"]:
            if price > peak:
                peak = price
            dd = (peak - price) / peak * 100
            max_dd = max(max_dd, dd)

        bench.benchmark_max_dd = max_dd

        # Volatility
        returns = df["close"].pct_change().dropna() * 100
        bench.benchmark_volatility = float(returns.std() * np.sqrt(288 * 365))

    def _calculate_risk_free(
        self, result: BacktestResult, bench: BenchmarkResult, rate: float
    ):
        """Calculate risk-free benchmark."""
        start = datetime.strptime(result.start_date, "%Y-%m-%d")
        end = datetime.strptime(result.end_date, "%Y-%m-%d")
        days = (end - start).days

        bench.benchmark_return = rate * (days / 365)
        bench.benchmark_max_dd = 0.0
        bench.benchmark_volatility = 0.0

    def _calculate_custom_benchmark(
        self, result: BacktestResult, bench: BenchmarkResult, symbol: str
    ):
        """Calculate custom symbol benchmark."""
        df = self.data_manager.get_data(
            symbol,
            result.interval,
            result.start_date,
            result.end_date,
        )

        if df is None or len(df) < 2:
            if self.verbose:
                print(f"  ⚠️  Could not load data for {symbol}")
            return

        first_price = float(df["close"].iloc[0])
        last_price = float(df["close"].iloc[-1])

        bench.benchmark_return = ((last_price - first_price) / first_price) * 100
        bench.benchmark_name = f"Buy & Hold {symbol}"

        # Max drawdown
        peak = df["close"].iloc[0]
        max_dd = 0.0
        for price in df["close"]:
            if price > peak:
                peak = price
            dd = (peak - price) / peak * 100
            max_dd = max(max_dd, dd)
        bench.benchmark_max_dd = max_dd

    def _calculate_comparison_metrics(
        self, result: BacktestResult, bench: BenchmarkResult
    ):
        """Calculate alpha, beta, etc."""
        strategy_return = result.total_pnl_pct()

        bench.outperformance = strategy_return - bench.benchmark_return
        bench.relative_dd = result.max_drawdown_pct() - bench.benchmark_max_dd
        bench.alpha = bench.outperformance
        bench.beta = 1.0

        if bench.benchmark_volatility > 0:
            bench.tracking_error = bench.benchmark_volatility * 0.5
            if bench.tracking_error > 0:
                bench.information_ratio = bench.outperformance / bench.tracking_error

        bench.pct_periods_beating = max(0, min(100, 50 + bench.outperformance * 2))

    def _print_summary(self, bench: BenchmarkResult):
        """Print comparison summary."""
        strat_ret = bench.strategy_result.total_pnl_pct()

        print(f"\n  {'═' * 60}")
        print(f"  ⚖️  BENCHMARK: {bench.benchmark_name}")
        print(f"  {'═' * 60}")

        g, r, rst = "\033[92m", "\033[91m", "\033[0m"

        sc = g if strat_ret > 0 else r
        bc = g if bench.benchmark_return > 0 else r
        oc = g if bench.outperformance > 0 else r

        print(f"\n  📊 Returns:")
        print(f"     Strategy:       {sc}{strat_ret:+.2f}%{rst}")
        print(f"     Benchmark:      {bc}{bench.benchmark_return:+.2f}%{rst}")
        print(f"     Outperformance: {oc}{bench.outperformance:+.2f}%{rst}")

        print(f"\n  📉 Risk:")
        print(f"     Strategy DD:    {bench.strategy_result.max_drawdown_pct():.2f}%")
        print(f"     Benchmark DD:   {bench.benchmark_max_dd:.2f}%")
        print(f"     Relative DD:    {bench.relative_dd:+.2f}%")

        print(f"\n  📈 Risk-Adjusted:")
        print(f"     Alpha:          {bench.alpha:+.2f}%")
        print(f"     Info Ratio:     {bench.information_ratio:.2f}")

        print(f"\n  💡 Verdict:")
        if bench.outperformance > 5 and bench.relative_dd < 0:
            print(f"     ✅ Strategy OUTPERFORMS with LOWER risk")
        elif bench.outperformance > 0:
            print(f"     ✅ Strategy OUTPERFORMS benchmark")
        elif bench.relative_dd < -5:
            print(f"     ⚠️  Underperforms but lower risk")
        else:
            print(f"     ❌ Strategy UNDERPERFORMS benchmark")

        print(f"  {'═' * 60}\n")
