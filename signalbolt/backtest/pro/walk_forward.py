"""
Walk-Forward Analysis for overfitting detection.

Splits data into rolling train/test windows and measures
performance degradation between in-sample and out-of-sample.

PRO Feature.
"""

import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, List, Dict

from signalbolt.backtest.engine import BacktestEngine, BacktestConfig, BacktestResult
from signalbolt.backtest.data_manager import DataManager
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.backtest.pro.walk_forward")


@dataclass
class WalkForwardWindow:
    """Single walk-forward window result."""

    window_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str

    train_result: Optional[BacktestResult] = None
    test_result: Optional[BacktestResult] = None

    train_return: float = 0.0
    test_return: float = 0.0
    train_sharpe: float = 0.0
    test_sharpe: float = 0.0
    train_winrate: float = 0.0
    test_winrate: float = 0.0
    train_trades: int = 0
    test_trades: int = 0

    return_degradation: float = 0.0
    sharpe_degradation: float = 0.0

    @property
    def is_profitable(self) -> bool:
        return self.test_return > 0

    def to_dict(self) -> Dict:
        return {
            "window_id": self.window_id,
            "train_period": f"{self.train_start} → {self.train_end}",
            "test_period": f"{self.test_start} → {self.test_end}",
            "train_return": self.train_return,
            "test_return": self.test_return,
            "train_trades": self.train_trades,
            "test_trades": self.test_trades,
            "return_degradation": self.return_degradation,
            "is_profitable": self.is_profitable,
        }


@dataclass
class WalkForwardResult:
    """Complete walk-forward analysis result."""

    symbol: str
    interval: str
    config_name: str
    start_date: str
    end_date: str

    num_windows: int = 5
    train_pct: float = 70.0

    windows: List[WalkForwardWindow] = field(default_factory=list)

    # Aggregate metrics
    avg_train_return: float = 0.0
    avg_test_return: float = 0.0
    avg_degradation: float = 0.0
    consistency_score: float = 0.0
    robustness_score: float = 0.0

    # Overfitting detection
    overfitting_detected: bool = False
    overfitting_severity: str = "none"

    def calculate_metrics(self):
        """Calculate aggregate metrics."""
        if not self.windows:
            return

        self.avg_train_return = np.mean([w.train_return for w in self.windows])
        self.avg_test_return = np.mean([w.test_return for w in self.windows])

        # Degradation
        degradations = []
        for w in self.windows:
            if abs(w.train_return) > 0.1:
                deg = (w.test_return - w.train_return) / abs(w.train_return) * 100
                degradations.append(deg)
                w.return_degradation = deg

        self.avg_degradation = np.mean(degradations) if degradations else 0

        # Consistency
        profitable = sum(1 for w in self.windows if w.is_profitable)
        self.consistency_score = (profitable / len(self.windows)) * 100

        # Robustness score
        score = 0
        score += self.consistency_score * 0.4
        score += max(0, min(30, self.avg_test_return * 2))
        score += max(0, 30 + self.avg_degradation * 0.3)
        self.robustness_score = max(0, min(100, score))

        # Overfitting detection
        if self.avg_degradation < -50:
            self.overfitting_detected = True
            self.overfitting_severity = "severe"
        elif self.avg_degradation < -30:
            self.overfitting_detected = True
            self.overfitting_severity = "moderate"
        elif self.avg_degradation < -15:
            self.overfitting_detected = True
            self.overfitting_severity = "mild"
        else:
            self.overfitting_detected = False
            self.overfitting_severity = "none"

    def summary(self) -> Dict:
        return {
            "symbol": self.symbol,
            "config": self.config_name,
            "num_windows": self.num_windows,
            "avg_train_return": self.avg_train_return,
            "avg_test_return": self.avg_test_return,
            "avg_degradation": self.avg_degradation,
            "consistency_score": self.consistency_score,
            "robustness_score": self.robustness_score,
            "overfitting_detected": self.overfitting_detected,
            "overfitting_severity": self.overfitting_severity,
        }

    def to_dict(self) -> Dict:
        return {
            "summary": self.summary(),
            "windows": [w.to_dict() for w in self.windows],
        }


class WalkForwardAnalyzer:
    """
    Walk-Forward Analysis for overfitting detection.

    Usage:
        analyzer = WalkForwardAnalyzer()
        result = analyzer.run(
            config=config,
            symbol='BTCUSDT',
            start_date='2022-01-01',
            end_date='2023-12-31',
            num_windows=5
        )
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.data_manager = DataManager(verbose=False)

    def run(
        self,
        config: BacktestConfig,
        symbol: str,
        start_date: str,
        end_date: str,
        interval: str = "5m",
        num_windows: int = 5,
        train_pct: float = 70.0,
        initial_balance: float = 1000.0,
    ) -> WalkForwardResult:
        """Run walk-forward analysis."""

        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        total_days = (end_dt - start_dt).days

        window_days = total_days // num_windows
        train_days = int(window_days * train_pct / 100)
        test_days = window_days - train_days

        config_name = getattr(config, "config_file", "Custom")
        if "/" in config_name or "\\" in config_name:
            config_name = config_name.split("/")[-1].split("\\")[-1]

        if self.verbose:
            print(f"\n  📈 Walk-Forward Analysis")
            print(f"  {'─' * 60}")
            print(f"     Symbol:      {symbol}")
            print(f"     Config:      {config_name}")
            print(f"     Period:      {start_date} → {end_date} ({total_days} days)")
            print(f"     Windows:     {num_windows}")
            print(f"     Train/Test:  {train_days}d / {test_days}d per window")
            print()

        wf_result = WalkForwardResult(
            symbol=symbol,
            interval=interval,
            config_name=config_name,
            start_date=start_date,
            end_date=end_date,
            num_windows=num_windows,
            train_pct=train_pct,
        )

        current_start = start_dt

        for w in range(num_windows):
            window_start = current_start
            train_end = window_start + timedelta(days=train_days)
            test_start = train_end + timedelta(days=1)
            test_end = test_start + timedelta(days=test_days)

            if test_end > end_dt:
                test_end = end_dt

            if self.verbose:
                print(f"  Window {w + 1}/{num_windows}: ", end="")
                print(
                    f"Train {window_start.strftime('%Y-%m-%d')} → {train_end.strftime('%Y-%m-%d')} | ",
                    end="",
                )
                print(
                    f"Test {test_start.strftime('%Y-%m-%d')} → {test_end.strftime('%Y-%m-%d')}"
                )

            # Train backtest
            engine = BacktestEngine(config, verbose=False)
            train_result = engine.run(
                symbol=symbol,
                interval=interval,
                start_date=window_start.strftime("%Y-%m-%d"),
                end_date=train_end.strftime("%Y-%m-%d"),
                period_name=f"Train W{w + 1}",
                initial_balance=initial_balance,
            )

            # Test backtest
            test_result = engine.run(
                symbol=symbol,
                interval=interval,
                start_date=test_start.strftime("%Y-%m-%d"),
                end_date=test_end.strftime("%Y-%m-%d"),
                period_name=f"Test W{w + 1}",
                initial_balance=initial_balance,
            )

            window = WalkForwardWindow(
                window_id=w + 1,
                train_start=window_start.strftime("%Y-%m-%d"),
                train_end=train_end.strftime("%Y-%m-%d"),
                test_start=test_start.strftime("%Y-%m-%d"),
                test_end=test_end.strftime("%Y-%m-%d"),
                train_result=train_result,
                test_result=test_result,
                train_return=train_result.total_pnl_pct(),
                test_return=test_result.total_pnl_pct(),
                train_sharpe=train_result.sharpe_ratio(),
                test_sharpe=test_result.sharpe_ratio(),
                train_winrate=train_result.winrate(),
                test_winrate=test_result.winrate(),
                train_trades=train_result.total_trades(),
                test_trades=test_result.total_trades(),
            )

            wf_result.windows.append(window)

            if self.verbose:
                train_color = "\033[92m" if window.train_return > 0 else "\033[91m"
                test_color = "\033[92m" if window.test_return > 0 else "\033[91m"
                reset = "\033[0m"
                print(
                    f"           Train: {train_color}{window.train_return:+.2f}%{reset} | "
                    f"Test: {test_color}{window.test_return:+.2f}%{reset}"
                )

            current_start = current_start + timedelta(days=window_days)

        wf_result.calculate_metrics()

        if self.verbose:
            self._print_summary(wf_result)

        return wf_result

    def _print_summary(self, wf: WalkForwardResult):
        """Print walk-forward summary."""
        print(f"\n  {'═' * 60}")
        print(f"  📊 WALK-FORWARD RESULTS")
        print(f"  {'═' * 60}")

        print(f"\n  📈 Performance:")
        print(f"     Avg Train Return:   {wf.avg_train_return:+.2f}%")
        print(f"     Avg Test Return:    {wf.avg_test_return:+.2f}%")
        print(f"     Avg Degradation:    {wf.avg_degradation:+.1f}%")

        print(f"\n  🎯 Scores:")
        print(f"     Consistency:        {wf.consistency_score:.0f}%")
        print(f"     Robustness:         {wf.robustness_score:.0f}/100")

        print(f"\n  🔍 Overfitting Analysis:")
        if wf.overfitting_detected:
            colors = {
                "mild": "\033[93m",
                "moderate": "\033[91m",
                "severe": "\033[91m\033[1m",
            }
            color = colors.get(wf.overfitting_severity, "")
            reset = "\033[0m"
            print(
                f"     ⚠️  Overfitting {color}DETECTED{reset}: {wf.overfitting_severity.upper()}"
            )
        else:
            print(f"     ✅ No significant overfitting detected")

        print(f"  {'═' * 60}\n")
