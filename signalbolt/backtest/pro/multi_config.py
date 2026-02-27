"""
Multi-Config Comparison Testing.

Compare multiple configurations on the same data period.
Generate ranking and comparison reports.

PRO Feature.
"""

import json
import numpy as np
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional

from signalbolt.backtest.engine import BacktestEngine, BacktestConfig, BacktestResult
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.backtest.pro.multi_config")


@dataclass
class ConfigComparisonReport:
    """Single config comparison entry."""

    config_name: str
    config_path: str
    result: Optional[BacktestResult] = None
    error: Optional[str] = None

    # Key metrics for comparison
    total_return: float = 0.0
    total_trades: int = 0
    winrate: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: float = 0.0
    expectancy: float = 0.0
    avg_trade: float = 0.0

    # Ranking
    rank: int = 0
    score: float = 0.0  # Composite score

    def calculate_metrics(self):
        """Calculate metrics from result."""
        if self.result is None:
            return

        self.total_return = self.result.total_pnl_pct()
        self.total_trades = self.result.total_trades()
        self.winrate = self.result.winrate()
        self.profit_factor = self.result.profit_factor()
        self.sharpe_ratio = self.result.sharpe_ratio()
        self.sortino_ratio = self.result.sortino_ratio()
        self.max_drawdown = self.result.max_drawdown_pct()
        self.expectancy = self.result.expectancy()
        self.avg_trade = self.result.avg_trade_pnl()

        # Calculate composite score (weighted)
        self.score = (
            self.total_return * 0.25
            + self.sharpe_ratio * 10 * 0.20
            + self.winrate * 0.15
            + self.profit_factor * 5 * 0.15
            + (100 - self.max_drawdown) * 0.15
            + self.expectancy * 10 * 0.10
        )

    def to_dict(self) -> Dict:
        return {
            "config_name": self.config_name,
            "rank": self.rank,
            "score": round(self.score, 2),
            "total_return": round(self.total_return, 2),
            "total_trades": self.total_trades,
            "winrate": round(self.winrate, 2),
            "profit_factor": round(self.profit_factor, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "sortino_ratio": round(self.sortino_ratio, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "expectancy": round(self.expectancy, 2),
            "avg_trade": round(self.avg_trade, 2),
            "error": self.error,
        }


@dataclass
class MultiConfigResult:
    """Complete multi-config comparison result."""

    symbol: str
    interval: str
    start_date: str
    end_date: str
    initial_balance: float = 1000.0

    configs: List[ConfigComparisonReport] = field(default_factory=list)

    # Best performers
    best_return_config: str = ""
    best_sharpe_config: str = ""
    best_drawdown_config: str = ""
    most_trades_config: str = ""

    def calculate_rankings(self):
        """Calculate rankings and find best performers."""
        # Filter successful configs
        valid = [c for c in self.configs if c.result is not None]

        if not valid:
            return

        # Sort by composite score
        valid.sort(key=lambda x: x.score, reverse=True)

        for i, cfg in enumerate(valid, 1):
            cfg.rank = i

        # Find best in categories
        self.best_return_config = max(valid, key=lambda x: x.total_return).config_name
        self.best_sharpe_config = max(valid, key=lambda x: x.sharpe_ratio).config_name
        self.best_drawdown_config = min(valid, key=lambda x: x.max_drawdown).config_name
        self.most_trades_config = max(valid, key=lambda x: x.total_trades).config_name

    def get_ranking_table(self) -> List[Dict]:
        """Get ranking as table data."""
        valid = [c for c in self.configs if c.result is not None]
        valid.sort(key=lambda x: x.rank)
        return [c.to_dict() for c in valid]

    def summary(self) -> Dict:
        """Get summary."""
        valid = [c for c in self.configs if c.result is not None]
        failed = [c for c in self.configs if c.error is not None]

        return {
            "symbol": self.symbol,
            "period": f"{self.start_date} → {self.end_date}",
            "configs_tested": len(self.configs),
            "configs_successful": len(valid),
            "configs_failed": len(failed),
            "best_overall": valid[0].config_name if valid else None,
            "best_return": self.best_return_config,
            "best_sharpe": self.best_sharpe_config,
            "best_drawdown": self.best_drawdown_config,
        }

    def to_dict(self) -> Dict:
        """Convert to dict for JSON export."""
        return {
            "summary": self.summary(),
            "ranking": self.get_ranking_table(),
            "detailed_results": {
                c.config_name: c.result.to_dict() if c.result else {"error": c.error}
                for c in self.configs
            },
        }

    def save_json(self, output_dir: Path) -> Path:
        """Save results to JSON file."""
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"multi_config_{self.symbol}_{timestamp}.json"
        filepath = output_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)

        return filepath


class MultiConfigTester:
    """
    Compare multiple configurations on the same data.

    Usage:
        tester = MultiConfigTester()
        result = tester.run(
            config_paths=['/path/to/config1.yaml', '/path/to/config2.yaml'],
            symbol='BTCUSDT',
            start_date='2023-01-01',
            end_date='2023-12-31'
        )
        result.save_json(Path('results/'))
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose

    def run(
        self,
        config_paths: List[str],
        symbol: str,
        start_date: str,
        end_date: str,
        interval: str = "5m",
        initial_balance: float = 1000.0,
    ) -> MultiConfigResult:
        """
        Run comparison on multiple configs.

        Args:
            config_paths: List of paths to config YAML files
            symbol: Trading symbol
            start_date: Start date
            end_date: End date
            interval: Candle interval
            initial_balance: Starting balance

        Returns:
            MultiConfigResult with all comparisons
        """
        if self.verbose:
            print(f"\n  ⚖️  Multi-Config Comparison")
            print(f"  {'═' * 60}")
            print(f"     Symbol:      {symbol}")
            print(f"     Period:      {start_date} → {end_date}")
            print(f"     Configs:     {len(config_paths)}")
            print(f"     Balance:     ${initial_balance:,.2f}")
            print(f"  {'═' * 60}\n")

        mc_result = MultiConfigResult(
            symbol=symbol,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            initial_balance=initial_balance,
        )

        for i, config_path in enumerate(config_paths, 1):
            config_name = Path(config_path).stem

            if self.verbose:
                print(
                    f"  [{i}/{len(config_paths)}] Testing {config_name}...",
                    end=" ",
                    flush=True,
                )

            report = ConfigComparisonReport(
                config_name=config_name,
                config_path=config_path,
            )

            try:
                config = BacktestConfig.from_yaml(config_path)
                engine = BacktestEngine(config, verbose=False)

                result = engine.run(
                    symbol=symbol,
                    interval=interval,
                    start_date=start_date,
                    end_date=end_date,
                    period_name=f"{config_name}",
                    initial_balance=initial_balance,
                )

                report.result = result
                report.calculate_metrics()

                if self.verbose:
                    ret = report.total_return
                    c = "\033[92m" if ret > 0 else "\033[91m"
                    r = "\033[0m"
                    print(
                        f"{c}{ret:+.2f}%{r} | {report.total_trades} trades | "
                        f"WR: {report.winrate:.1f}% | Sharpe: {report.sharpe_ratio:.2f}"
                    )

            except Exception as e:
                report.error = str(e)
                if self.verbose:
                    print(f"❌ {e}")

            mc_result.configs.append(report)

        # Calculate rankings
        mc_result.calculate_rankings()

        if self.verbose:
            self._print_summary(mc_result)

        return mc_result

    def _print_summary(self, mc: MultiConfigResult):
        """Print comparison summary with ranking table."""
        print(f"\n  {'═' * 70}")
        print(f"  🏆 MULTI-CONFIG RANKING")
        print(f"  {'═' * 70}")

        # Header
        print(
            f"\n  {'Rank':<6}{'Config':<25}{'Return':>10}{'Trades':>8}{'WR%':>8}{'Sharpe':>8}{'MaxDD':>8}{'Score':>8}"
        )
        print(f"  {'-' * 70}")

        # Rows
        valid = [c for c in mc.configs if c.result is not None]
        valid.sort(key=lambda x: x.rank)

        for cfg in valid:
            ret_c = "\033[92m" if cfg.total_return > 0 else "\033[91m"
            rst = "\033[0m"

            # Medal for top 3
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(cfg.rank, "  ")

            print(
                f"  {medal}{cfg.rank:<4}"
                f"{cfg.config_name:<25}"
                f"{ret_c}{cfg.total_return:>+9.2f}%{rst}"
                f"{cfg.total_trades:>8}"
                f"{cfg.winrate:>7.1f}%"
                f"{cfg.sharpe_ratio:>8.2f}"
                f"{cfg.max_drawdown:>7.2f}%"
                f"{cfg.score:>8.1f}"
            )

        # Failed configs
        failed = [c for c in mc.configs if c.error is not None]
        if failed:
            print(f"\n  ❌ Failed configs:")
            for cfg in failed:
                print(f"     • {cfg.config_name}: {cfg.error[:50]}...")

        # Best in category
        print(f"\n  🏅 Best in Category:")
        print(f"     💰 Highest Return:    {mc.best_return_config}")
        print(f"     📈 Best Sharpe:       {mc.best_sharpe_config}")
        print(f"     🛡️  Lowest Drawdown:  {mc.best_drawdown_config}")
        print(f"     📊 Most Trades:       {mc.most_trades_config}")

        # Winner
        if valid:
            winner = valid[0]
            print(f"\n  {'═' * 70}")
            print(f"  👑 WINNER: {winner.config_name}")
            print(
                f"     Return: {winner.total_return:+.2f}% | "
                f"Sharpe: {winner.sharpe_ratio:.2f} | "
                f"Score: {winner.score:.1f}"
            )

        print(f"  {'═' * 70}\n")
