"""
Multi-Symbol Batch Testing.

Test strategy on multiple symbols simultaneously.

PRO Feature.
"""

import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict

from signalbolt.backtest.engine import BacktestEngine, BacktestConfig, BacktestResult
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.backtest.pro.multi_symbol")


@dataclass
class MultiSymbolResult:
    """Results from multi-symbol batch test."""

    config_name: str = ""
    start_date: str = ""
    end_date: str = ""

    symbols: List[str] = field(default_factory=list)
    results: Dict[str, BacktestResult] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)

    # Aggregate stats
    total_trades: int = 0
    avg_return: float = 0.0
    avg_winrate: float = 0.0
    avg_sharpe: float = 0.0
    avg_drawdown: float = 0.0
    profitable_symbols: int = 0

    def calculate_aggregates(self):
        """Calculate aggregate statistics."""
        if not self.results:
            return

        returns = []
        winrates = []
        sharpes = []
        drawdowns = []

        for symbol, result in self.results.items():
            self.total_trades += result.total_trades()
            returns.append(result.total_pnl_pct())
            winrates.append(result.winrate())
            sharpes.append(result.sharpe_ratio())
            drawdowns.append(result.max_drawdown_pct())

            if result.total_pnl_pct() > 0:
                self.profitable_symbols += 1

        self.avg_return = float(np.mean(returns)) if returns else 0
        self.avg_winrate = float(np.mean(winrates)) if winrates else 0
        self.avg_sharpe = float(np.mean(sharpes)) if sharpes else 0
        self.avg_drawdown = float(np.mean(drawdowns)) if drawdowns else 0

    def get_ranking(self) -> List[tuple]:
        """Get symbols ranked by return."""
        return sorted(
            [(s, r.total_pnl_pct()) for s, r in self.results.items()],
            key=lambda x: x[1],
            reverse=True,
        )

    def summary(self) -> Dict:
        return {
            "config": self.config_name,
            "period": f"{self.start_date} → {self.end_date}",
            "symbols_tested": len(self.symbols),
            "symbols_successful": len(self.results),
            "symbols_failed": len(self.errors),
            "profitable_symbols": self.profitable_symbols,
            "total_trades": self.total_trades,
            "avg_return": self.avg_return,
            "avg_winrate": self.avg_winrate,
            "avg_sharpe": self.avg_sharpe,
            "avg_drawdown": self.avg_drawdown,
        }

    def to_dict(self) -> Dict:
        return {
            "summary": self.summary(),
            "ranking": self.get_ranking(),
            "results": {s: r.to_dict() for s, r in self.results.items()},
            "errors": self.errors,
        }


class MultiSymbolTester:
    """
    Batch test strategy on multiple symbols.

    Usage:
        tester = MultiSymbolTester()
        result = tester.run(
            config=config,
            symbols=['BTCUSDT', 'ETHUSDT', 'SOLUSDT'],
            start_date='2023-01-01',
            end_date='2023-12-31'
        )
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose

    def run(
        self,
        config: BacktestConfig,
        symbols: List[str],
        start_date: str,
        end_date: str,
        interval: str = "5m",
        initial_balance: float = 1000.0,
    ) -> MultiSymbolResult:
        """Run batch test on multiple symbols."""

        config_name = getattr(config, "config_file", "Custom")
        if "/" in config_name or "\\" in config_name:
            config_name = config_name.split("/")[-1].split("\\")[-1]

        if self.verbose:
            print(f"\n  🔬 Multi-Symbol Batch Test")
            print(f"  {'─' * 60}")
            print(f"     Config:  {config_name}")
            print(f"     Symbols: {len(symbols)}")
            print(f"     Period:  {start_date} → {end_date}")
            print()

        ms_result = MultiSymbolResult(
            config_name=config_name,
            start_date=start_date,
            end_date=end_date,
            symbols=symbols,
        )

        for i, symbol in enumerate(symbols, 1):
            if self.verbose:
                print(
                    f"  [{i}/{len(symbols)}] Testing {symbol}...", end=" ", flush=True
                )

            try:
                engine = BacktestEngine(config, verbose=False)
                result = engine.run(
                    symbol=symbol,
                    interval=interval,
                    start_date=start_date,
                    end_date=end_date,
                    initial_balance=initial_balance,
                )

                ms_result.results[symbol] = result

                if self.verbose:
                    ret = result.total_pnl_pct()
                    c = "\033[92m" if ret > 0 else "\033[91m"
                    r = "\033[0m"
                    print(f"{c}{ret:+.2f}%{r} ({result.total_trades()} trades)")

            except Exception as e:
                ms_result.errors[symbol] = str(e)
                if self.verbose:
                    print(f"❌ {e}")

        ms_result.calculate_aggregates()

        if self.verbose:
            self._print_summary(ms_result)

        return ms_result

    def _print_summary(self, ms: MultiSymbolResult):
        """Print multi-symbol summary."""
        print(f"\n  {'═' * 60}")
        print(f"  🔬 MULTI-SYMBOL RESULTS")
        print(f"  {'═' * 60}")

        pct_profitable = (
            ms.profitable_symbols / len(ms.results) * 100 if ms.results else 0
        )

        print(f"\n  📊 Aggregate Stats:")
        print(f"     Symbols Tested:    {len(ms.symbols)}")
        print(f"     Successful:        {len(ms.results)}")
        print(
            f"     Profitable:        {ms.profitable_symbols} ({pct_profitable:.0f}%)"
        )
        print(f"     Total Trades:      {ms.total_trades}")
        print(f"     Avg Return:        {ms.avg_return:+.2f}%")
        print(f"     Avg Winrate:       {ms.avg_winrate:.1f}%")
        print(f"     Avg Sharpe:        {ms.avg_sharpe:.2f}")
        print(f"     Avg Drawdown:      {ms.avg_drawdown:.2f}%")

        print(f"\n  🏆 Top 5 Performers:")
        ranking = ms.get_ranking()[:5]
        for i, (symbol, ret) in enumerate(ranking, 1):
            c = "\033[92m" if ret > 0 else "\033[91m"
            r = "\033[0m"
            print(f"     {i}. {symbol:<12} {c}{ret:+.2f}%{r}")

        if ms.errors:
            print(f"\n  ❌ Failed: {', '.join(ms.errors.keys())}")

        print(f"  {'═' * 60}\n")
