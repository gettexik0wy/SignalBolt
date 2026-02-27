"""
Monte Carlo Simulation for strategy validation.

Tests strategy robustness by:
1. Shuffling trade order (trade independence test)
2. Resampling with replacement (bootstrap)
3. Random subsetting (stability test)

PRO Feature.
"""

import numpy as np
import random
from dataclasses import dataclass, field
from typing import Optional, List, Dict

from signalbolt.backtest.engine import BacktestResult
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.backtest.pro.monte_carlo")


@dataclass
class MonteCarloResult:
    """Results from Monte Carlo simulation."""

    # Original result
    original_result: BacktestResult

    # Simulation parameters
    num_simulations: int = 1000
    method: str = "shuffle"
    confidence_level: float = 95.0

    # Simulated distributions
    final_balances: List[float] = field(default_factory=list)
    max_drawdowns: List[float] = field(default_factory=list)
    total_returns: List[float] = field(default_factory=list)
    sharpe_ratios: List[float] = field(default_factory=list)

    # Percentile statistics
    percentiles: Dict[str, Dict[str, float]] = field(default_factory=dict)

    # Risk metrics
    risk_of_ruin: float = 0.0
    probability_of_profit: float = 0.0
    expected_return: float = 0.0
    worst_case_drawdown: float = 0.0

    def calculate_percentiles(self):
        """Calculate percentile statistics."""
        percentile_levels = [5, 10, 25, 50, 75, 90, 95]

        self.percentiles = {
            "final_balance": {},
            "max_drawdown": {},
            "total_return": {},
            "sharpe_ratio": {},
        }

        for p in percentile_levels:
            self.percentiles["final_balance"][f"p{p}"] = float(
                np.percentile(self.final_balances, p)
            )
            self.percentiles["max_drawdown"][f"p{p}"] = float(
                np.percentile(self.max_drawdowns, p)
            )
            self.percentiles["total_return"][f"p{p}"] = float(
                np.percentile(self.total_returns, p)
            )
            if self.sharpe_ratios:
                self.percentiles["sharpe_ratio"][f"p{p}"] = float(
                    np.percentile(self.sharpe_ratios, p)
                )

    def summary(self) -> Dict:
        """Get summary statistics."""
        return {
            "num_simulations": self.num_simulations,
            "method": self.method,
            "original_return": self.original_result.total_pnl_pct(),
            "expected_return": self.expected_return,
            "probability_of_profit": self.probability_of_profit,
            "risk_of_ruin": self.risk_of_ruin,
            "worst_case_drawdown": self.worst_case_drawdown,
            "confidence_interval": {
                "lower": self.percentiles.get("total_return", {}).get("p5", 0),
                "upper": self.percentiles.get("total_return", {}).get("p95", 0),
            },
            "median_return": self.percentiles.get("total_return", {}).get("p50", 0),
            "median_drawdown": self.percentiles.get("max_drawdown", {}).get("p50", 0),
        }

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "summary": self.summary(),
            "percentiles": self.percentiles,
            "distributions": {
                "returns_sample": self.total_returns[:100],  # First 100 for viz
                "drawdowns_sample": self.max_drawdowns[:100],
            },
        }


class MonteCarloSimulator:
    """
    Monte Carlo simulation for strategy validation.

    Usage:
        simulator = MonteCarloSimulator()
        mc_result = simulator.run(backtest_result, num_simulations=1000)
        print(f"Risk of Ruin: {mc_result.risk_of_ruin:.1f}%")
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose

    def run(
        self,
        result: BacktestResult,
        num_simulations: int = 1000,
        method: str = "shuffle",
        subset_pct: float = 80.0,
        ruin_threshold: float = 50.0,
        random_seed: Optional[int] = None,
    ) -> MonteCarloResult:
        """
        Run Monte Carlo simulation.

        Args:
            result: Original backtest result
            num_simulations: Number of simulations to run
            method: 'shuffle', 'bootstrap', or 'subset'
            subset_pct: Percentage of trades for 'subset' method
            ruin_threshold: Percentage loss considered as ruin
            random_seed: Random seed for reproducibility

        Returns:
            MonteCarloResult with all statistics
        """
        if random_seed is not None:
            np.random.seed(random_seed)
            random.seed(random_seed)

        if not result.trades:
            raise ValueError("No trades in backtest result")

        trades = result.trades
        initial_balance = result.initial_balance
        fee_pct = result.config.total_fee_pct

        # Extract trade returns
        trade_returns = [t.net_pnl_pct(fee_pct) for t in trades]
        trade_sizes = [t.size_usd / initial_balance for t in trades]

        mc_result = MonteCarloResult(
            original_result=result,
            num_simulations=num_simulations,
            method=method,
        )

        if self.verbose:
            print(
                f"\n  🎲 Running Monte Carlo ({method}) with {num_simulations} simulations..."
            )

        ruin_count = 0
        profit_count = 0

        for i in range(num_simulations):
            if self.verbose and (i + 1) % 200 == 0:
                pct = (i + 1) / num_simulations * 100
                print(f"\r  ⏳ Progress: {pct:.0f}%", end="", flush=True)

            # Generate simulated trade sequence
            if method == "shuffle":
                indices = np.random.permutation(len(trade_returns))
                sim_returns = [trade_returns[j] for j in indices]
                sim_sizes = [trade_sizes[j] for j in indices]

            elif method == "bootstrap":
                indices = np.random.choice(
                    len(trade_returns), size=len(trade_returns), replace=True
                )
                sim_returns = [trade_returns[j] for j in indices]
                sim_sizes = [trade_sizes[j] for j in indices]

            elif method == "subset":
                n_subset = max(1, int(len(trade_returns) * subset_pct / 100))
                indices = np.random.choice(
                    len(trade_returns), size=n_subset, replace=False
                )
                sim_returns = [trade_returns[j] for j in indices]
                sim_sizes = [trade_sizes[j] for j in indices]
            else:
                raise ValueError(f"Unknown method: {method}")

            # Simulate equity curve
            balance = initial_balance
            peak = initial_balance
            max_dd = 0.0
            ruined = False

            for ret, size in zip(sim_returns, sim_sizes):
                pnl = balance * size * (ret / 100)
                balance += pnl

                if balance > peak:
                    peak = balance
                dd = (peak - balance) / peak * 100 if peak > 0 else 0
                max_dd = max(max_dd, dd)

                if balance <= initial_balance * (1 - ruin_threshold / 100):
                    ruined = True
                    ruin_count += 1
                    break

            mc_result.final_balances.append(balance)
            mc_result.max_drawdowns.append(max_dd)

            total_return = (balance - initial_balance) / initial_balance * 100
            mc_result.total_returns.append(total_return)

            if total_return > 0 and not ruined:
                profit_count += 1

            if len(sim_returns) > 1:
                std = np.std(sim_returns)
                sharpe = np.mean(sim_returns) / std if std > 0 else 0
                mc_result.sharpe_ratios.append(sharpe)

        if self.verbose:
            print()

        # Calculate final statistics
        mc_result.risk_of_ruin = (ruin_count / num_simulations) * 100
        mc_result.probability_of_profit = (profit_count / num_simulations) * 100
        mc_result.expected_return = float(np.mean(mc_result.total_returns))
        mc_result.worst_case_drawdown = float(np.max(mc_result.max_drawdowns))

        mc_result.calculate_percentiles()

        if self.verbose:
            self._print_summary(mc_result)

        return mc_result

    def _print_summary(self, mc: MonteCarloResult):
        """Print Monte Carlo summary."""
        print(f"\n  {'═' * 60}")
        print(f"  🎲 MONTE CARLO RESULTS ({mc.num_simulations} simulations)")
        print(f"  {'═' * 60}")

        orig_ret = mc.original_result.total_pnl_pct()

        print(f"\n  📊 Return Distribution:")
        print(f"     Original:     {orig_ret:+.2f}%")
        print(f"     Expected:     {mc.expected_return:+.2f}%")
        print(f"     Median:       {mc.percentiles['total_return']['p50']:+.2f}%")

        p5 = mc.percentiles["total_return"]["p5"]
        p95 = mc.percentiles["total_return"]["p95"]
        print(f"     95% CI:       [{p5:+.2f}%, {p95:+.2f}%]")

        print(f"\n  📉 Risk Metrics:")
        print(f"     P(Profit):    {mc.probability_of_profit:.1f}%")
        print(f"     Risk of Ruin: {mc.risk_of_ruin:.1f}%")
        print(f"     Worst DD:     {mc.worst_case_drawdown:.2f}%")
        print(f"     Median DD:    {mc.percentiles['max_drawdown']['p50']:.2f}%")

        print(f"\n  💡 Interpretation:")
        if mc.probability_of_profit >= 70 and mc.risk_of_ruin < 5:
            print(f"     ✅ Strategy appears ROBUST")
        elif mc.probability_of_profit >= 55 and mc.risk_of_ruin < 15:
            print(f"     ⚠️  Strategy is MODERATE - proceed with caution")
        else:
            print(f"     ❌ Strategy shows HIGH RISK - review parameters")

        print(f"  {'═' * 60}\n")
