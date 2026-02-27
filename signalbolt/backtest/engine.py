"""
Backtest engine - core simulation logic.

Features:
- No look-ahead bias
- Fill on next bar open
- Dynamic slippage based on regime
- Realistic spread simulation
- Full trade history
- Indicator snapshots at entry/exit
- Multi-timeframe indicator tracking

Usage:
    config = BacktestConfig.from_yaml('configs/config_safe.yaml')
    engine = BacktestEngine(config)
    result = engine.run(
        symbol='BTCUSDT',
        interval='5m',
        start_date='2022-01-01',
        end_date='2022-12-31'
    )
    print(f"P&L: {result.total_pnl_pct():.2f}%")
"""

import time
import numpy as np
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Callable
from pathlib import Path
from enum import Enum

from signalbolt.backtest.data_manager import DataManager
from signalbolt.core.indicators import IndicatorValues, IndicatorCalculator  #
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.backtest.engine")


# =============================================================================
# MARKET REGIME
# =============================================================================


class MarketRegime(Enum):
    """Market regime types."""

    BULL = "bull"
    BEAR = "bear"
    RANGE = "range"
    CRASH = "crash"


@dataclass
class RegimeConfig:
    """Parameters for specific market regime."""

    regime: MarketRegime

    # Risk management
    hard_sl_pct: float
    be_activation_pct: float
    trail_distance_pct: float
    timeout_minutes: int
    min_profit_to_close_pct: float

    # Scanner
    min_signal_score: float
    signal_cooldown_min: int

    # Execution realism
    slippage_bps: float
    spread_bps: float


class RegimeDetector:
    """
    Detect market regime WITHOUT look-ahead bias.
    Uses only data available at each point in time.
    """

    def __init__(
        self,
        lookback_days: int = 45,
        bull_threshold: float = 25.0,
        bear_threshold: float = -15.0,
        crash_threshold: float = -20.0,
    ):
        self.lookback_days = lookback_days
        self.bull_threshold = bull_threshold
        self.bear_threshold = bear_threshold
        self.crash_threshold = crash_threshold

        self._running_peak = None
        self._last_reset_idx = 0

    def detect(self, df: pd.DataFrame, current_idx: int) -> MarketRegime:
        """
        Detect regime at current_idx using ONLY data up to that point.

        Args:
            df: OHLCV dataframe
            current_idx: Current position in dataframe

        Returns:
            MarketRegime enum
        """
        candles_per_day = 288  # 5m candles
        lookback_candles = self.lookback_days * candles_per_day
        crash_lookback = 10 * candles_per_day

        if current_idx < lookback_candles:
            return MarketRegime.RANGE

        recent = df.iloc[current_idx - lookback_candles : current_idx]
        crash_window = df.iloc[current_idx - crash_lookback : current_idx]

        if len(recent) < 10:
            return MarketRegime.RANGE

        current_price = float(recent["close"].iloc[-1])

        # Price changes
        price_change_30d = (current_price / float(recent["close"].iloc[0]) - 1) * 100
        price_change_7d = (
            (current_price / float(crash_window["close"].iloc[0]) - 1) * 100
            if len(crash_window) > 0
            else 0
        )

        # Running peak (no look-ahead)
        if (
            self._running_peak is None
            or (current_idx - self._last_reset_idx) > lookback_candles
        ):
            self._running_peak = float(recent["close"].max())
            self._last_reset_idx = current_idx

        if current_price > self._running_peak:
            self._running_peak = current_price

        drawdown = (current_price / self._running_peak - 1) * 100

        # Decision tree
        if price_change_7d < self.crash_threshold or drawdown < -20:
            return MarketRegime.CRASH

        if price_change_30d > self.bull_threshold and drawdown > -5:
            return MarketRegime.BULL

        if price_change_30d < self.bear_threshold:
            return MarketRegime.BEAR

        return MarketRegime.RANGE

    def reset(self):
        """Reset detector state."""
        self._running_peak = None
        self._last_reset_idx = 0


class RegimePresets:
    """Predefined regime configurations."""

    @staticmethod
    def get(regime: MarketRegime) -> RegimeConfig:
        """Get config for regime."""
        presets = {
            MarketRegime.BULL: RegimeConfig(
                regime=MarketRegime.BULL,
                hard_sl_pct=-3.0,
                be_activation_pct=0.8,
                trail_distance_pct=0.6,
                timeout_minutes=150,
                min_profit_to_close_pct=0.4,
                min_signal_score=58,
                signal_cooldown_min=20,
                slippage_bps=5,
                spread_bps=10,
            ),
            MarketRegime.BEAR: RegimeConfig(
                regime=MarketRegime.BEAR,
                hard_sl_pct=-1.8,
                be_activation_pct=0.25,
                trail_distance_pct=0.25,
                timeout_minutes=60,
                min_profit_to_close_pct=0.2,
                min_signal_score=70,
                signal_cooldown_min=40,
                slippage_bps=10,
                spread_bps=25,
            ),
            MarketRegime.RANGE: RegimeConfig(
                regime=MarketRegime.RANGE,
                hard_sl_pct=-2.2,
                be_activation_pct=0.4,
                trail_distance_pct=0.35,
                timeout_minutes=90,
                min_profit_to_close_pct=0.25,
                min_signal_score=65,
                signal_cooldown_min=30,
                slippage_bps=7,
                spread_bps=15,
            ),
            MarketRegime.CRASH: RegimeConfig(
                regime=MarketRegime.CRASH,
                hard_sl_pct=-1.5,
                be_activation_pct=0.2,
                trail_distance_pct=0.2,
                timeout_minutes=45,
                min_profit_to_close_pct=0.15,
                min_signal_score=75,
                signal_cooldown_min=60,
                slippage_bps=50,
                spread_bps=200,
            ),
        }
        return presets.get(regime, presets[MarketRegime.RANGE])


# =============================================================================
# BACKTEST CONFIG
# =============================================================================


@dataclass
class BacktestConfig:
    """Configuration for backtest run."""

    # Position sizing
    wallet_pct: float = 85.0

    # Risk management
    hard_sl_pct: float = -2.0
    be_activation_pct: float = 0.5
    trail_distance_pct: float = 0.4
    timeout_minutes: int = 60

    # Scanner
    min_signal_score: float = 65.0
    signal_cooldown_min: int = 30

    # Fees
    taker_fee_pct: float = 0.04
    maker_fee_pct: float = 0.0

    # Execution mode
    adaptive_regime: bool = True
    conservative_execution: bool = False

    #  Indicator snapshot settings
    save_indicator_snapshots: bool = True
    include_mtf_snapshots: bool = False
    mtf_snapshot_timeframes: List[str] = field(
        default_factory=lambda: ["5m", "15m", "1h"]
    )

    # Source file (for reference)
    config_file: str = ""

    @classmethod
    def from_yaml(cls, path: str) -> "BacktestConfig":
        """
        Load config from YAML file.

        Args:
            path: Path to YAML config file

        Returns:
            BacktestConfig instance
        """
        import yaml

        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}

        spot = data.get("spot", {})
        scanner = data.get("scanner", {})
        exchange = data.get("exchange", {})

        #  Indicator snapshot settings
        snapshots = data.get("backtest", {}).get("indicator_snapshots", {})

        return cls(
            wallet_pct=spot.get("wallet_pct", 85.0),
            hard_sl_pct=spot.get("hard_sl_pct", -2.0),
            be_activation_pct=spot.get("be_activation_pct", 0.5),
            trail_distance_pct=spot.get("trail_distance_pct", 0.4),
            timeout_minutes=spot.get("timeout_minutes", 60),
            min_signal_score=scanner.get("min_signal_score", 65.0),
            signal_cooldown_min=scanner.get("signal_cooldown_min", 30),
            taker_fee_pct=exchange.get("taker_fee_pct", 0.04),
            maker_fee_pct=exchange.get("maker_fee_pct", 0.0),
            save_indicator_snapshots=snapshots.get("enabled", True),
            include_mtf_snapshots=snapshots.get("include_mtf", False),
            mtf_snapshot_timeframes=snapshots.get(
                "mtf_timeframes", ["5m", "15m", "1h"]
            ),
            config_file=path,
        )

    @property
    def total_fee_pct(self) -> float:
        """Total round-trip fee percentage."""
        single = 0.7 * self.maker_fee_pct + 0.3 * self.taker_fee_pct
        return single * 2

    def to_dict(self) -> dict:
        """Convert to dict."""
        return {
            "wallet_pct": self.wallet_pct,
            "hard_sl_pct": self.hard_sl_pct,
            "be_activation_pct": self.be_activation_pct,
            "trail_distance_pct": self.trail_distance_pct,
            "timeout_minutes": self.timeout_minutes,
            "min_signal_score": self.min_signal_score,
            "signal_cooldown_min": self.signal_cooldown_min,
            "taker_fee_pct": self.taker_fee_pct,
            "maker_fee_pct": self.maker_fee_pct,
            "adaptive_regime": self.adaptive_regime,
            "conservative_execution": self.conservative_execution,
            #
            "save_indicator_snapshots": self.save_indicator_snapshots,
            "include_mtf_snapshots": self.include_mtf_snapshots,
            "mtf_snapshot_timeframes": self.mtf_snapshot_timeframes,
            "config_file": self.config_file,
        }


# =============================================================================
# BACKTEST TRADE
# =============================================================================


@dataclass
class BacktestTrade:
    """Single trade in backtest."""

    # Identification
    trade_id: int
    symbol: str
    direction: str  # 'LONG' or 'SHORT'

    # Entry
    entry_time: datetime
    entry_price: float
    entry_score: float
    entry_regime: str = "unknown"

    # Exit
    exit_time: Optional[datetime] = None
    exit_price: float = 0.0
    exit_reason: str = ""
    exit_regime: str = "unknown"

    # Position sizing
    size_usd: float = 0.0
    quantity: float = 0.0

    # Price tracking
    highest_price: float = 0.0
    highest_pnl_pct: float = 0.0
    lowest_price: float = float("inf")

    # Trailing stop state
    trailing_active: bool = False
    current_sl_pct: float = -2.5

    # Execution costs
    entry_slippage_bps: float = 0.0
    exit_slippage_bps: float = 0.0
    spread_cost_bps: float = 0.0

    #  Indicator snapshots at entry/exit
    entry_indicators: Optional[IndicatorValues] = None
    exit_indicators: Optional[IndicatorValues] = None

    #  MTF indicator snapshots (Dict[timeframe, IndicatorValues])
    entry_mtf_indicators: Optional[Dict[str, IndicatorValues]] = None
    exit_mtf_indicators: Optional[Dict[str, IndicatorValues]] = None

    def pnl_pct(self) -> float:
        """Gross P&L percentage."""
        if self.exit_price <= 0 or self.entry_price <= 0:
            return 0.0

        if self.direction == "LONG":
            return ((self.exit_price - self.entry_price) / self.entry_price) * 100
        else:
            return ((self.entry_price - self.exit_price) / self.entry_price) * 100

    def pnl_usd(self) -> float:
        """Gross P&L in USD."""
        return self.size_usd * (self.pnl_pct() / 100)

    def net_pnl_pct(self, fee_pct: float) -> float:
        """Net P&L percentage after fees and costs."""
        gross = self.pnl_pct()
        slippage = (self.entry_slippage_bps + self.exit_slippage_bps) / 100
        spread = self.spread_cost_bps / 100
        return gross - fee_pct - slippage - spread

    def net_pnl_usd(self, fee_pct: float) -> float:
        """Net P&L in USD after fees and costs."""
        return self.size_usd * (self.net_pnl_pct(fee_pct) / 100)

    def hold_time_minutes(self) -> float:
        """Position hold time in minutes."""
        if self.exit_time is None:
            return 0.0
        return (self.exit_time - self.entry_time).total_seconds() / 60

    def is_winner(self, fee_pct: float) -> bool:
        """Check if trade is a winner (after costs)."""
        return self.net_pnl_pct(fee_pct) > 0.05

    def is_loser(self, fee_pct: float) -> bool:
        """Check if trade is a loser (after costs)."""
        return self.net_pnl_pct(fee_pct) < -0.05

    #  Helper methods for indicator analysis
    def get_indicator_delta(self, indicator_name: str) -> Optional[float]:
        """
        Get change in indicator value from entry to exit.

        Args:
            indicator_name: 'rsi', 'adx', 'volume_ratio', 'atr_pct', etc.

        Returns:
            Delta value or None if indicator not available
        """
        if not self.entry_indicators or not self.exit_indicators:
            return None

        entry_val = getattr(self.entry_indicators, indicator_name, None)
        exit_val = getattr(self.exit_indicators, indicator_name, None)

        if entry_val is None or exit_val is None:
            return None

        return exit_val - entry_val

    def get_indicator_deltas(self) -> Dict[str, float]:
        """Get all core indicator deltas."""
        if not self.entry_indicators or not self.exit_indicators:
            return {}

        indicators = ["rsi", "adx", "di_plus", "di_minus", "atr_pct", "volume_ratio"]
        deltas = {}

        for ind in indicators:
            delta = self.get_indicator_delta(ind)
            if delta is not None:
                deltas[ind] = delta

        # Calculate EMA alignment gap
        try:
            entry_gap = (
                (self.entry_indicators.ema9 - self.entry_indicators.ema21)
                / self.entry_indicators.ema21
                * 100
            )
            exit_gap = (
                (self.exit_indicators.ema9 - self.exit_indicators.ema21)
                / self.exit_indicators.ema21
                * 100
            )
            deltas["ema_alignment_pct"] = exit_gap - entry_gap
        except (ZeroDivisionError, AttributeError):
            pass

        return deltas

    def analyze_exit_cause(self) -> Dict[str, any]:
        """
        Intelligent analysis of why trade exited.

        Returns dict with:
        - 'root_cause': str (primary reason)
        - 'contributing_factors': List[str]
        - 'lessons': List[str]
        - 'severity': str ('minor', 'moderate', 'critical')
        """
        if not self.entry_indicators or not self.exit_indicators:
            return {
                "root_cause": "unknown",
                "contributing_factors": [],
                "lessons": ["No indicator data available"],
                "severity": "minor",
                "deltas": {},
            }

        deltas = self.get_indicator_deltas()
        factors = []
        lessons = []
        severity = "minor"

        # RSI Analysis
        rsi_delta = deltas.get("rsi", 0)
        if rsi_delta > 15:
            factors.append(f"RSI climbed to overbought (+{rsi_delta:.1f} points)")
            lessons.append("Consider tighter trailing stop when RSI > 65")
            severity = "moderate"
        elif rsi_delta < -15:
            factors.append(f"RSI crashed ({rsi_delta:.1f} points)")
            lessons.append("Price exhausted, momentum reversed")

        # ADX Analysis
        adx_delta = deltas.get("adx", 0)
        if adx_delta < -5:
            factors.append(
                f"ADX weakened by {abs(adx_delta):.1f} points (trend fading)"
            )
            lessons.append("Activate breakeven when ADX starts declining")
            if adx_delta < -8:
                severity = "moderate"

        #  Volume Analysis
        vol_delta = deltas.get("volume_ratio", 0)
        if vol_delta < -0.5:
            factors.append(f"Volume dried up (ratio fell by {abs(vol_delta):.1f}x)")
            lessons.append("Exit when volume drops below 1.0x on lower timeframes")

        #  EMA Alignment
        ema_delta = deltas.get("ema_alignment_pct", 0)
        if ema_delta < -0.3:
            factors.append(f"EMA alignment narrowed by {abs(ema_delta):.2f}%")
            lessons.append("Consider partial exit when EMAs start converging")

        #  Determine root cause based on exit_reason
        root_cause = "normal_exit"

        if "HARD_SL" in self.exit_reason:
            root_cause = "stop_loss_hit"
            severity = "critical"
            lessons.insert(
                0, "⚠️ Position moved against entry thesis - verify setup next time"
            )

        elif "TIMEOUT" in self.exit_reason:
            if self.highest_pnl_pct > 0.8:
                root_cause = "momentum_exhaustion"
                lessons.insert(
                    0, "💡 Trail stop was too loose - missed optimal exit window"
                )
            else:
                root_cause = "no_momentum"
                lessons.insert(
                    0, "⚠️ Entry signal was weak or market conditions changed quickly"
                )

        elif "TRAIL" in self.exit_reason:
            root_cause = "trailing_stop_hit"
            if len(factors) > 2:
                severity = "moderate"
                lessons.insert(
                    0, "📊 Multiple indicators deteriorated before exit - good trail"
                )
            else:
                lessons.insert(0, " Clean exit via trailing stop")

        return {
            "root_cause": root_cause,
            "contributing_factors": factors,
            "lessons": lessons,
            "severity": severity,
            "deltas": deltas,
        }

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        result = {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "entry_price": round(self.entry_price, 8),
            "entry_score": round(self.entry_score, 1),
            "entry_regime": self.entry_regime,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "exit_price": round(self.exit_price, 8),
            "exit_reason": self.exit_reason,
            "exit_regime": self.exit_regime,
            "size_usd": round(self.size_usd, 2),
            "quantity": round(self.quantity, 8),
            "pnl_pct": round(self.pnl_pct(), 2),
            "highest_pnl_pct": round(self.highest_pnl_pct, 2),
            "hold_time_min": round(self.hold_time_minutes(), 1),
            "trailing_active": self.trailing_active,
        }

        #  Add indicator snapshots if available
        if self.entry_indicators:
            result["entry_indicators"] = self.entry_indicators.to_dict()

        if self.exit_indicators:
            result["exit_indicators"] = self.exit_indicators.to_dict()

        #  Add MTF snapshots if available
        if self.entry_mtf_indicators:
            result["entry_mtf_indicators"] = {
                tf: ind.to_dict() for tf, ind in self.entry_mtf_indicators.items()
            }

        if self.exit_mtf_indicators:
            result["exit_mtf_indicators"] = {
                tf: ind.to_dict() for tf, ind in self.exit_mtf_indicators.items()
            }

        return result


# =============================================================================
# BACKTEST RESULT
# =============================================================================


@dataclass
class BacktestResult:
    """Complete backtest results."""

    # Metadata
    symbol: str
    interval: str
    period_name: str
    start_date: str
    end_date: str
    config: BacktestConfig

    # Capital
    initial_balance: float = 1000.0
    final_balance: float = 1000.0

    # Trades
    trades: List[BacktestTrade] = field(default_factory=list)

    # Equity tracking
    equity_curve: List[Tuple[datetime, float]] = field(default_factory=list)

    # Statistics
    total_candles: int = 0
    signals_generated: int = 0
    signals_executed: int = 0
    signals_rejected_score: int = 0
    signals_rejected_spread: int = 0

    # Regime tracking
    regime_distribution: Dict[str, int] = field(default_factory=dict)
    regime_changes: List[Tuple[datetime, str, str]] = field(default_factory=list)

    # Costs
    total_slippage_cost: float = 0.0
    total_spread_cost: float = 0.0

    # Timing
    backtest_duration_sec: float = 0.0

    # =========================================================================
    # BASIC STATS
    # =========================================================================

    def total_trades(self) -> int:
        """Total number of closed trades."""
        return len(self.trades)

    def winning_trades(self, fee_pct: float = None) -> int:
        """Number of winning trades."""
        fee = fee_pct if fee_pct is not None else self.config.total_fee_pct
        return sum(1 for t in self.trades if t.is_winner(fee))

    def losing_trades(self, fee_pct: float = None) -> int:
        """Number of losing trades."""
        fee = fee_pct if fee_pct is not None else self.config.total_fee_pct
        return sum(1 for t in self.trades if t.is_loser(fee))

    def winrate(self, fee_pct: float = None) -> float:
        """Win rate percentage."""
        total = self.total_trades()
        if total == 0:
            return 0.0
        return (self.winning_trades(fee_pct) / total) * 100

    def total_pnl_pct(self) -> float:
        """Total P&L percentage."""
        if self.initial_balance <= 0:
            return 0.0
        return (
            (self.final_balance - self.initial_balance) / self.initial_balance
        ) * 100

    def total_pnl_usd(self) -> float:
        """Total P&L in USD."""
        return self.final_balance - self.initial_balance

    # =========================================================================
    # ADVANCED STATS
    # =========================================================================

    def max_drawdown_pct(self) -> float:
        """Maximum drawdown percentage."""
        if not self.equity_curve:
            return 0.0

        peak = self.initial_balance
        max_dd = 0.0

        for _, equity in self.equity_curve:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)

        return max_dd

    def profit_factor(self, fee_pct: float = None) -> float:
        """Profit factor (gross profit / gross loss)."""
        fee = fee_pct if fee_pct is not None else self.config.total_fee_pct

        gross_profit = sum(
            t.net_pnl_usd(fee) for t in self.trades if t.net_pnl_usd(fee) > 0
        )
        gross_loss = abs(
            sum(t.net_pnl_usd(fee) for t in self.trades if t.net_pnl_usd(fee) < 0)
        )

        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0

        return gross_profit / gross_loss

    def sharpe_ratio(self, fee_pct: float = None) -> float:
        """Sharpe ratio (simplified)."""
        if len(self.trades) < 2:
            return 0.0

        fee = fee_pct if fee_pct is not None else self.config.total_fee_pct
        returns = [t.net_pnl_pct(fee) for t in self.trades]

        mean_ret = np.mean(returns)
        std_ret = np.std(returns)

        if std_ret == 0:
            return 0.0

        # Annualize (assume ~200 trades/year)
        trades_per_year = min(200, len(returns))
        factor = (
            np.sqrt(trades_per_year / len(returns))
            if len(returns) < trades_per_year
            else 1
        )

        return (mean_ret / std_ret) * factor

    def sortino_ratio(self, fee_pct: float = None) -> float:
        """Sortino ratio (uses downside deviation)."""
        if len(self.trades) < 2:
            return 0.0

        fee = fee_pct if fee_pct is not None else self.config.total_fee_pct
        returns = [t.net_pnl_pct(fee) for t in self.trades]

        mean_ret = np.mean(returns)
        downside_returns = [r for r in returns if r < 0]

        if not downside_returns:
            return float("inf") if mean_ret > 0 else 0.0

        downside_dev = np.sqrt(np.mean([r**2 for r in downside_returns]))

        if downside_dev == 0:
            return 0.0

        return mean_ret / downside_dev

    def avg_trade_pnl(self, fee_pct: float = None) -> float:
        """Average trade P&L percentage."""
        if not self.trades:
            return 0.0
        fee = fee_pct if fee_pct is not None else self.config.total_fee_pct
        return sum(t.net_pnl_pct(fee) for t in self.trades) / len(self.trades)

    def avg_winner_pnl(self, fee_pct: float = None) -> float:
        """Average winning trade P&L."""
        fee = fee_pct if fee_pct is not None else self.config.total_fee_pct
        winners = [t for t in self.trades if t.is_winner(fee)]
        if not winners:
            return 0.0
        return sum(t.net_pnl_pct(fee) for t in winners) / len(winners)

    def avg_loser_pnl(self, fee_pct: float = None) -> float:
        """Average losing trade P&L."""
        fee = fee_pct if fee_pct is not None else self.config.total_fee_pct
        losers = [t for t in self.trades if t.is_loser(fee)]
        if not losers:
            return 0.0
        return sum(t.net_pnl_pct(fee) for t in losers) / len(losers)

    def avg_hold_time(self) -> float:
        """Average hold time in minutes."""
        if not self.trades:
            return 0.0
        return sum(t.hold_time_minutes() for t in self.trades) / len(self.trades)

    def best_trade(self, fee_pct: float = None) -> float:
        """Best trade P&L percentage."""
        if not self.trades:
            return 0.0
        fee = fee_pct if fee_pct is not None else self.config.total_fee_pct
        return max(t.net_pnl_pct(fee) for t in self.trades)

    def worst_trade(self, fee_pct: float = None) -> float:
        """Worst trade P&L percentage."""
        if not self.trades:
            return 0.0
        fee = fee_pct if fee_pct is not None else self.config.total_fee_pct
        return min(t.net_pnl_pct(fee) for t in self.trades)

    def max_consecutive_wins(self, fee_pct: float = None) -> int:
        """Maximum consecutive winning trades."""
        if not self.trades:
            return 0

        fee = fee_pct if fee_pct is not None else self.config.total_fee_pct
        max_streak = 0
        current_streak = 0

        for t in self.trades:
            if t.is_winner(fee):
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0

        return max_streak

    def max_consecutive_losses(self, fee_pct: float = None) -> int:
        """Maximum consecutive losing trades."""
        if not self.trades:
            return 0

        fee = fee_pct if fee_pct is not None else self.config.total_fee_pct
        max_streak = 0
        current_streak = 0

        for t in self.trades:
            if t.is_loser(fee):
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0

        return max_streak

    def expectancy(self, fee_pct: float = None) -> float:
        """Expectancy (expected P&L per trade)."""
        if not self.trades:
            return 0.0

        fee = fee_pct if fee_pct is not None else self.config.total_fee_pct
        wr = self.winrate(fee) / 100
        avg_win = self.avg_winner_pnl(fee)
        avg_loss = abs(self.avg_loser_pnl(fee))

        return (wr * avg_win) - ((1 - wr) * avg_loss)

    # =========================================================================
    # REGIME ANALYSIS
    # =========================================================================

    def performance_by_regime(self, fee_pct: float = None) -> Dict[str, dict]:
        """Get performance breakdown by regime."""
        fee = fee_pct if fee_pct is not None else self.config.total_fee_pct
        stats = {}

        for regime_name in ["bull", "bear", "range", "crash"]:
            regime_trades = [t for t in self.trades if t.entry_regime == regime_name]

            if not regime_trades:
                continue

            wins = sum(1 for t in regime_trades if t.is_winner(fee))
            total_pnl = sum(t.net_pnl_pct(fee) for t in regime_trades)

            stats[regime_name] = {
                "trades": len(regime_trades),
                "wins": wins,
                "losses": len(regime_trades) - wins,
                "winrate": (wins / len(regime_trades) * 100),
                "total_pnl": total_pnl,
                "avg_pnl": total_pnl / len(regime_trades),
            }

        return stats

    def performance_by_exit_reason(self, fee_pct: float = None) -> Dict[str, dict]:
        """Get performance breakdown by exit reason."""
        fee = fee_pct if fee_pct is not None else self.config.total_fee_pct
        stats = {}

        for trade in self.trades:
            reason = trade.exit_reason.split("_")[0] if trade.exit_reason else "UNKNOWN"

            if reason not in stats:
                stats[reason] = {
                    "trades": 0,
                    "wins": 0,
                    "total_pnl": 0.0,
                }

            stats[reason]["trades"] += 1
            stats[reason]["total_pnl"] += trade.net_pnl_pct(fee)

            if trade.is_winner(fee):
                stats[reason]["wins"] += 1

        # Calculate averages
        for reason in stats:
            if stats[reason]["trades"] > 0:
                stats[reason]["avg_pnl"] = (
                    stats[reason]["total_pnl"] / stats[reason]["trades"]
                )
                stats[reason]["winrate"] = (
                    stats[reason]["wins"] / stats[reason]["trades"]
                ) * 100

        return stats

    # =========================================================================
    # SERIALIZATION
    # =========================================================================

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        fee = self.config.total_fee_pct

        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "period_name": self.period_name,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "config": self.config.to_dict(),
            "initial_balance": round(self.initial_balance, 2),
            "final_balance": round(self.final_balance, 2),
            "total_pnl_pct": round(self.total_pnl_pct(), 2),
            "total_pnl_usd": round(self.total_pnl_usd(), 2),
            "total_trades": self.total_trades(),
            "winning_trades": self.winning_trades(),
            "losing_trades": self.losing_trades(),
            "winrate": round(self.winrate(), 2),
            "profit_factor": round(self.profit_factor(), 2),
            "max_drawdown": round(self.max_drawdown_pct(), 2),
            "sharpe_ratio": round(self.sharpe_ratio(), 2),
            "sortino_ratio": round(self.sortino_ratio(), 2),
            "avg_trade_pnl": round(self.avg_trade_pnl(), 2),
            "avg_winner_pnl": round(self.avg_winner_pnl(), 2),
            "avg_loser_pnl": round(self.avg_loser_pnl(), 2),
            "avg_hold_time_min": round(self.avg_hold_time(), 1),
            "best_trade": round(self.best_trade(), 2),
            "worst_trade": round(self.worst_trade(), 2),
            "max_consecutive_wins": self.max_consecutive_wins(),
            "max_consecutive_losses": self.max_consecutive_losses(),
            "expectancy": round(self.expectancy(), 2),
            "total_candles": self.total_candles,
            "signals_generated": self.signals_generated,
            "signals_executed": self.signals_executed,
            "signals_rejected_score": self.signals_rejected_score,
            "signals_rejected_spread": self.signals_rejected_spread,
            "regime_distribution": self.regime_distribution,
            "total_slippage_cost": round(self.total_slippage_cost, 2),
            "total_spread_cost": round(self.total_spread_cost, 2),
            "backtest_duration_sec": round(self.backtest_duration_sec, 1),
            "performance_by_regime": self.performance_by_regime(),
            "performance_by_exit_reason": self.performance_by_exit_reason(),
            "trades": [t.to_dict() for t in self.trades],
        }


# =============================================================================
# SIGNAL SCANNER
# =============================================================================


class SignalScanner:
    """
    Generate trading signals based on technical indicators.

    Uses EMA alignment, ADX, RSI, and volume confirmation.
    """

    def __init__(self, min_score: float = 65.0):
        """
        Initialize scanner.

        Args:
            min_score: Minimum score threshold
        """
        self.min_score = min_score

    def scan(self, df: pd.DataFrame, symbol: str) -> Optional[dict]:
        """
        Scan for signals on dataframe.

        IMPORTANT: df should contain ONLY data up to current point (no look-ahead).

        Args:
            df: OHLCV dataframe
            symbol: Trading symbol

        Returns:
            Signal dict or None
        """
        if len(df) < 50:
            return None

        # Use last 100 candles for calculation
        df = df.tail(100).copy()

        # Calculate indicators
        df["EMA9"] = ta.ema(df["close"], length=9)
        df["EMA21"] = ta.ema(df["close"], length=21)
        df["EMA50"] = ta.ema(df["close"], length=50)

        rsi = ta.rsi(df["close"], length=14)
        adx_data = ta.adx(df["high"], df["low"], df["close"], length=14)
        atr = ta.atr(df["high"], df["low"], df["close"], length=14)

        last = df.iloc[-1]
        price = float(last["close"])

        # Get indicator values
        ema9 = float(last["EMA9"]) if pd.notna(last["EMA9"]) else price
        ema21 = float(last["EMA21"]) if pd.notna(last["EMA21"]) else price
        ema50 = float(last["EMA50"]) if pd.notna(last["EMA50"]) else price

        rsi_val = (
            float(rsi.iloc[-1]) if rsi is not None and pd.notna(rsi.iloc[-1]) else 50
        )

        adx_val = 20.0
        di_plus = 0.0
        di_minus = 0.0

        if adx_data is not None:
            if "ADX_14" in adx_data.columns and pd.notna(adx_data["ADX_14"].iloc[-1]):
                adx_val = float(adx_data["ADX_14"].iloc[-1])
            if "DMP_14" in adx_data.columns and pd.notna(adx_data["DMP_14"].iloc[-1]):
                di_plus = float(adx_data["DMP_14"].iloc[-1])
            if "DMN_14" in adx_data.columns and pd.notna(adx_data["DMN_14"].iloc[-1]):
                di_minus = float(adx_data["DMN_14"].iloc[-1])

        atr_val = (
            float(atr.iloc[-1])
            if atr is not None and pd.notna(atr.iloc[-1])
            else price * 0.01
        )
        atr_pct = (atr_val / price) * 100

        # Volume analysis
        avg_vol = (
            float(df["volume"].iloc[-20:].mean())
            if len(df) >= 20
            else float(df["volume"].mean())
        )
        current_vol = float(df["volume"].iloc[-1])
        volume_ratio = current_vol / avg_vol if avg_vol > 0 else 1.0

        # =====================================================================
        # DIRECTION CHECK
        # =====================================================================

        ema_aligned_bull = ema9 > ema21 > ema50
        di_bull = di_plus > di_minus

        # Only LONG signals for now
        if not (ema_aligned_bull and di_bull):
            return None

        direction = "LONG"

        # =====================================================================
        # SCORING
        # =====================================================================

        score = 0.0

        # EMA alignment (0-20 points)
        ema_gap = abs(ema9 - ema21) / ema21 * 100 if ema21 > 0 else 0
        if ema_gap > 0.5:
            score += 20
        elif ema_gap > 0.3:
            score += 15
        elif ema_gap > 0.1:
            score += 10
        else:
            score += 5

        # Price position (0-10 points)
        if price > ema9 > ema21:
            score += 10
        elif price > ema21:
            score += 5

        # ADX strength (0-30 points)
        if adx_val >= 50:
            score += 30
        elif adx_val >= 40:
            score += 25
        elif adx_val >= 35:
            score += 22
        elif adx_val >= 30:
            score += 18
        elif adx_val >= 25:
            score += 14
        elif adx_val >= 20:
            score += 10
        elif adx_val >= 15:
            score += 5

        # RSI (0-20 points)
        if 45 <= rsi_val <= 55:
            score += 20
        elif 40 <= rsi_val <= 60:
            score += 15
        elif 35 <= rsi_val <= 65:
            score += 10
        elif rsi_val > 70:
            score += 0
        else:
            score += 5

        # Volume (0-15 points)
        if volume_ratio >= 2.0:
            score += 15
        elif volume_ratio >= 1.5:
            score += 12
        elif volume_ratio >= 1.2:
            score += 8
        elif volume_ratio >= 1.0:
            score += 5
        else:
            score += 2

        # DI spread (0-5 points)
        di_spread = abs(di_plus - di_minus)
        if di_spread > 20:
            score += 5
        elif di_spread > 15:
            score += 4
        elif di_spread > 10:
            score += 3
        elif di_spread > 5:
            score += 1

        # =====================================================================
        # FILTERS
        # =====================================================================

        # Overbought filter
        if rsi_val > 75:
            return None

        # Low volatility filter
        if atr_pct < 0.2:
            return None

        return {
            "symbol": symbol,
            "direction": direction,
            "score": round(score, 1),
            "price": price,
            "atr_pct": round(atr_pct, 3),
            "rsi": round(rsi_val, 1),
            "adx": round(adx_val, 1),
            "di_plus": round(di_plus, 1),
            "di_minus": round(di_minus, 1),
            "volume_ratio": round(volume_ratio, 2),
            "ema9": round(ema9, 8),
            "ema21": round(ema21, 8),
            "ema50": round(ema50, 8),
        }


# =============================================================================
# BACKTEST ENGINE
# =============================================================================


class BacktestEngine:
    """
    Production backtest engine.

    Features:
    - No look-ahead bias
    - Fill on next bar open (not current close)
    - Dynamic slippage based on regime
    - Realistic spread simulation
    - Full trade history
    -  Indicator snapshots at entry/exit
    -  Multi-timeframe indicator tracking

    Usage:
        config = BacktestConfig.from_yaml('configs/config_safe.yaml')
        engine = BacktestEngine(config)

        result = engine.run(
            symbol='BTCUSDT',
            interval='5m',
            start_date='2022-01-01',
            end_date='2022-12-31',
            initial_balance=1000.0
        )

        print(f"P&L: {result.total_pnl_pct():.2f}%")
        print(f"Trades: {result.total_trades()}")
    """

    def __init__(
        self,
        config: BacktestConfig,
        verbose: bool = True,
        on_progress: Optional[Callable[[float, str], None]] = None,
    ):
        """
        Initialize backtest engine.

        Args:
            config: Backtest configuration
            verbose: Print progress to console
            on_progress: Optional callback for progress updates (progress_pct, message)
        """
        self.config = config
        self.verbose = verbose
        self.on_progress = on_progress

        # Components
        self.data_manager = DataManager(verbose=verbose)
        self.scanner = SignalScanner(config.min_signal_score)
        self.regime_detector = RegimeDetector()

        #  Indicator calculator
        self.indicator_calc = IndicatorCalculator()

        # State
        self.current_regime = MarketRegime.RANGE
        self.current_regime_config = RegimePresets.get(MarketRegime.RANGE)

        # Trade counter
        self._trade_counter = 0

    def get_min_candles(self, timeframe: str) -> int:
        """Get minimum candles required for timeframe."""
        # Map timeframe to required candles
        MIN_CANDLES_PER_TIMEFRAME = {
            "1m": 200,
            "3m": 150,
            "5m": 100,
            "15m": 100,
            "30m": 100,
            "1h": 100,
            "2h": 100,
            "4h": 100,
            "6h": 80,
            "8h": 80,
            "12h": 60,
            "1d": 60,
            "3d": 40,
            "1w": 30,
            "1M": 24,
        }
        return MIN_CANDLES_PER_TIMEFRAME.get(timeframe, 100)

    def run(
        self,
        symbol: str,
        interval: str,
        start_date: str,
        end_date: str,
        period_name: str = "",
        initial_balance: float = 1000.0,
    ) -> BacktestResult:
        """
        Run backtest on specified period.

        Args:
            symbol: Trading symbol (e.g., 'BTCUSDT')
            interval: Candle interval (e.g., '5m')
            start_date: Start date YYYY-MM-DD
            end_date: End date YYYY-MM-DD
            period_name: Optional name for this period
            initial_balance: Starting balance in USD

        Returns:
            BacktestResult with all trades and statistics
        """
        start_time = time.time()

        if not period_name:
            period_name = f"{symbol} ({start_date} to {end_date})"

        if self.verbose:
            print(f"\n{'═' * 70}")
            print(f"  BACKTEST: {symbol} [{interval}]")
            print(f"  Period: {period_name}")
            print(f"  Range: {start_date} → {end_date}")
            print(f"  Config: {self.config.config_file or 'Custom'}")
            print(f"  Balance: ${initial_balance:,.2f}")
            print(
                f"  Adaptive Regime: {'Yes' if self.config.adaptive_regime else 'No'}"
            )
            print(
                f"  Indicator Snapshots: {'Yes' if self.config.save_indicator_snapshots else 'No'}"
            )
            if (
                self.config.save_indicator_snapshots
                and self.config.include_mtf_snapshots
            ):
                print(
                    f"  MTF Snapshots: {', '.join(self.config.mtf_snapshot_timeframes)}"
                )
            print(f"{'═' * 70}\n")

        # Download data
        df = self.data_manager.get_data(symbol, interval, start_date, end_date)

        if df is None or len(df) < 100:
            if self.verbose:
                print(f"  ❌ Insufficient data for backtest")

            return BacktestResult(
                symbol=symbol,
                interval=interval,
                period_name=period_name,
                start_date=start_date,
                end_date=end_date,
                config=self.config,
                initial_balance=initial_balance,
                final_balance=initial_balance,
            )

        # Reset state
        self.regime_detector.reset()
        self.current_regime = MarketRegime.RANGE
        self.current_regime_config = RegimePresets.get(MarketRegime.RANGE)
        self._trade_counter = 0

        # Initialize result
        result = BacktestResult(
            symbol=symbol,
            interval=interval,
            period_name=period_name,
            start_date=start_date,
            end_date=end_date,
            config=self.config,
            initial_balance=initial_balance,
            final_balance=initial_balance,
            total_candles=len(df),
        )

        # Run simulation
        result = self._simulate(df, result)

        # Record timing
        result.backtest_duration_sec = time.time() - start_time

        if self.verbose:
            self._print_summary(result)

        return result

    def _simulate(self, df: pd.DataFrame, result: BacktestResult) -> BacktestResult:
        """Run candle-by-candle simulation."""
        balance = result.initial_balance
        position: Optional[BacktestTrade] = None
        cooldown_until: Optional[datetime] = None

        # Record initial equity
        result.equity_curve.append((df.index[0], balance))

        total = len(df)
        lookback = 100  # Candles needed for indicators

        # Regime check interval (1 day = 288 x 5m candles)
        regime_check_interval = 288
        last_regime_check = 0
        last_progress = 0

        for i in range(lookback, total - 1):  # -1 to allow next bar fill
            current_time = df.index[i]
            current = df.iloc[i]
            next_bar = df.iloc[i + 1]

            high = float(current["high"])
            low = float(current["low"])
            close = float(current["close"])

            # =====================================================================
            # PROGRESS
            # =====================================================================

            progress = int((i - lookback) / (total - lookback - 1) * 100)

            if self.verbose and progress >= last_progress + 5:
                regime_str = self.current_regime.value.upper()
                print(
                    f"\r  ⏳ {progress:3d}% | {regime_str:6s} | "
                    f"Trades: {len(result.trades):3d} | ${balance:,.2f}  ",
                    end="",
                    flush=True,
                )
                last_progress = progress

            if self.on_progress and progress != last_progress:
                self.on_progress(progress / 100, f"Processing candle {i}/{total}")

            # =====================================================================
            # REGIME DETECTION
            # =====================================================================

            if self.config.adaptive_regime and (
                i - last_regime_check >= regime_check_interval
            ):
                old_regime = self.current_regime
                new_regime = self.regime_detector.detect(df, i)

                if new_regime != old_regime:
                    self.current_regime = new_regime
                    self.current_regime_config = RegimePresets.get(new_regime)
                    self.scanner.min_score = self.current_regime_config.min_signal_score

                    result.regime_changes.append(
                        (current_time, old_regime.value, new_regime.value)
                    )

                # Track regime distribution
                regime_name = new_regime.value
                result.regime_distribution[regime_name] = (
                    result.regime_distribution.get(regime_name, 0)
                    + regime_check_interval
                )

                last_regime_check = i

            # =====================================================================
            # MANAGE OPEN POSITION
            # =====================================================================

            if position is not None:
                # Update price tracking
                if high > position.highest_price:
                    position.highest_price = high
                    pnl = ((high - position.entry_price) / position.entry_price) * 100
                    position.highest_pnl_pct = max(position.highest_pnl_pct, pnl)

                if low < position.lowest_price:
                    position.lowest_price = low

                # Check exit conditions
                exit_reason = self._check_exit(position, current_time, high, low, close)

                if exit_reason:
                    # Execute exit on NEXT bar
                    exit_price = self._simulate_exit_fill(next_bar, position.direction)

                    position.exit_time = current_time
                    position.exit_price = exit_price
                    position.exit_reason = exit_reason
                    position.exit_regime = self.current_regime.value
                    position.exit_slippage_bps = self.current_regime_config.slippage_bps
                    position.spread_cost_bps += (
                        self.current_regime_config.spread_bps / 2
                    )

                    #  Capture exit indicator snapshots
                    if self.config.save_indicator_snapshots:
                        try:
                            # Recalculate indicators at exit point
                            exit_history = df.iloc[i - lookback + 1 : i + 1]
                            exit_df_ind = self.indicator_calc.calculate(
                                exit_history, result.symbol
                            )
                            position.exit_indicators = self.indicator_calc.get_latest(
                                exit_df_ind
                            )

                            #  MTF snapshots at exit
                            if (
                                self.config.include_mtf_snapshots
                                and position.entry_mtf_indicators
                            ):
                                position.exit_mtf_indicators = {}

                                for tf in position.entry_mtf_indicators.keys():
                                    try:
                                        snapshot_end = current_time

                                        tf_df = self.data_manager.get_data(
                                            symbol=result.symbol,
                                            interval=tf,
                                            start_date=(
                                                snapshot_end - timedelta(days=30)
                                            ).strftime("%Y-%m-%d"),
                                            end_date=snapshot_end.strftime("%Y-%m-%d"),
                                        )

                                        if tf_df is not None and len(tf_df) >= 50:
                                            tf_df = tf_df[tf_df.index <= snapshot_end]

                                            if len(tf_df) >= 50:
                                                tf_df_ind = (
                                                    self.indicator_calc.calculate(
                                                        tf_df, result.symbol
                                                    )
                                                )
                                                position.exit_mtf_indicators[tf] = (
                                                    self.indicator_calc.get_latest(
                                                        tf_df_ind
                                                    )
                                                )

                                    except Exception as e:
                                        log.debug(
                                            f"Could not fetch {tf} exit indicators: {e}"
                                        )

                        except Exception as e:
                            log.warning(
                                f"Failed to capture exit indicators for {result.symbol}: {e}"
                            )

                    # Update balance
                    pnl_usd = position.net_pnl_usd(self.config.total_fee_pct)
                    balance += pnl_usd

                    # Track costs
                    result.total_slippage_cost += (
                        (position.exit_slippage_bps / 100) * position.size_usd / 100
                    )
                    result.total_spread_cost += (
                        (self.current_regime_config.spread_bps / 2 / 100)
                        * position.size_usd
                        / 100
                    )

                    # Save trade
                    result.trades.append(position)
                    result.equity_curve.append((current_time, balance))

                    # Start cooldown
                    cooldown_until = current_time + timedelta(
                        minutes=self.current_regime_config.signal_cooldown_min
                    )
                    position = None

            # =====================================================================
            # SCAN FOR NEW SIGNALS
            # =====================================================================

            if position is None:
                # Check cooldown
                if cooldown_until is not None and current_time < cooldown_until:
                    continue

                # Get historical data up to current point (NO LOOK-AHEAD)
                history = df.iloc[i - lookback + 1 : i + 1]
                signal = self.scanner.scan(history, result.symbol)

                if signal is not None:
                    result.signals_generated += 1

                    # Check score threshold (may have changed due to regime)
                    if signal["score"] < self.current_regime_config.min_signal_score:
                        result.signals_rejected_score += 1
                        continue

                    # Check spread/liquidity
                    if not self._check_liquidity(signal["atr_pct"]):
                        result.signals_rejected_spread += 1
                        continue

                    # Execute entry on NEXT bar
                    entry_price = self._simulate_entry_fill(
                        next_bar, signal["direction"]
                    )
                    size_usd = balance * (self.config.wallet_pct / 100)

                    self._trade_counter += 1

                    position = BacktestTrade(
                        trade_id=self._trade_counter,
                        symbol=result.symbol,
                        direction=signal["direction"],
                        entry_time=current_time,
                        entry_price=entry_price,
                        entry_score=signal["score"],
                        entry_regime=self.current_regime.value,
                        size_usd=size_usd,
                        quantity=size_usd / entry_price,
                        highest_price=entry_price,
                        lowest_price=entry_price,
                        current_sl_pct=self.current_regime_config.hard_sl_pct,
                        entry_slippage_bps=self.current_regime_config.slippage_bps,
                        spread_cost_bps=self.current_regime_config.spread_bps / 2,
                    )

                    # Track costs
                    result.total_slippage_cost += (
                        (self.current_regime_config.slippage_bps / 100) * size_usd / 100
                    )
                    result.total_spread_cost += (
                        (self.current_regime_config.spread_bps / 2 / 100)
                        * size_usd
                        / 100
                    )

                    result.signals_executed += 1

                    #  Capture entry indicator snapshots
                    if self.config.save_indicator_snapshots:
                        try:
                            # Calculate indicators on current history
                            df_with_ind = self.indicator_calc.calculate(
                                history, result.symbol
                            )
                            position.entry_indicators = self.indicator_calc.get_latest(
                                df_with_ind
                            )

                            #  MTF snapshots (if enabled)
                            if self.config.include_mtf_snapshots:
                                position.entry_mtf_indicators = {}

                                for tf in self.config.mtf_snapshot_timeframes:
                                    try:
                                        # Get enough historical data for this timeframe
                                        tf_lookback = self.get_min_candles(tf)

                                        # Calculate end time for this snapshot
                                        snapshot_end = current_time

                                        # Get data for this timeframe up to current point
                                        tf_df = self.data_manager.get_data(
                                            symbol=result.symbol,
                                            interval=tf,
                                            start_date=(
                                                snapshot_end - timedelta(days=30)
                                            ).strftime("%Y-%m-%d"),
                                            end_date=snapshot_end.strftime("%Y-%m-%d"),
                                        )

                                        if tf_df is not None and len(tf_df) >= 50:
                                            # Only use data up to current time (no look-ahead)
                                            tf_df = tf_df[tf_df.index <= snapshot_end]

                                            if len(tf_df) >= 50:
                                                tf_df_ind = (
                                                    self.indicator_calc.calculate(
                                                        tf_df, result.symbol
                                                    )
                                                )
                                                position.entry_mtf_indicators[tf] = (
                                                    self.indicator_calc.get_latest(
                                                        tf_df_ind
                                                    )
                                                )

                                    except Exception as e:
                                        log.debug(
                                            f"Could not fetch {tf} entry indicators for {result.symbol}: {e}"
                                        )

                        except Exception as e:
                            log.warning(
                                f"Failed to capture entry indicators for {result.symbol}: {e}"
                            )

        # =====================================================================
        # CLOSE FINAL POSITION
        # =====================================================================

        if position is not None:
            final_price = float(df.iloc[-1]["close"])

            position.exit_time = df.index[-1]
            position.exit_price = final_price
            position.exit_reason = "END_OF_DATA"
            position.exit_regime = self.current_regime.value

            #  Capture final exit indicators
            if self.config.save_indicator_snapshots:
                try:
                    final_history = df.iloc[-lookback:]
                    final_df_ind = self.indicator_calc.calculate(
                        final_history, result.symbol
                    )
                    position.exit_indicators = self.indicator_calc.get_latest(
                        final_df_ind
                    )
                except:
                    pass

            pnl_usd = position.net_pnl_usd(self.config.total_fee_pct)
            balance += pnl_usd

            result.trades.append(position)
            result.equity_curve.append((df.index[-1], balance))

        result.final_balance = balance

        if self.verbose:
            print()  # New line after progress

        return result

    def _simulate_entry_fill(self, next_bar: pd.Series, direction: str) -> float:
        """
        Simulate entry fill on next bar open with slippage.

        Args:
            next_bar: Next candle OHLCV
            direction: 'LONG' or 'SHORT'

        Returns:
            Simulated fill price
        """
        open_price = float(next_bar["open"])
        high_price = float(next_bar["high"])
        low_price = float(next_bar["low"])

        slippage_bps = self.current_regime_config.slippage_bps

        if self.config.conservative_execution:
            slippage_bps *= 3

        if direction == "LONG":
            # Buy slippage: price goes UP
            fill = open_price * (1 + slippage_bps / 10000)
            return min(fill, high_price)  # Can't fill higher than bar high
        else:
            # Sell slippage: price goes DOWN
            fill = open_price * (1 - slippage_bps / 10000)
            return max(fill, low_price)  # Can't fill lower than bar low

    def _simulate_exit_fill(self, next_bar: pd.Series, direction: str) -> float:
        """
        Simulate exit fill on next bar open with slippage.

        Args:
            next_bar: Next candle OHLCV
            direction: Original position direction

        Returns:
            Simulated fill price
        """
        open_price = float(next_bar["open"])
        high_price = float(next_bar["high"])
        low_price = float(next_bar["low"])

        slippage_bps = self.current_regime_config.slippage_bps

        if self.config.conservative_execution:
            slippage_bps *= 3

        if direction == "LONG":
            # Closing LONG = sell, price goes DOWN (adverse)
            fill = open_price * (1 - slippage_bps / 10000)
            return max(fill, low_price)
        else:
            # Closing SHORT = buy, price goes UP (adverse)
            fill = open_price * (1 + slippage_bps / 10000)
            return min(fill, high_price)

    def _check_liquidity(self, atr_pct: float) -> bool:
        """
        Check if spread is acceptable given current volatility.

        Args:
            atr_pct: ATR as percentage of price

        Returns:
            True if liquidity is acceptable
        """
        spread_bps = self.current_regime_config.spread_bps

        # Volatility multiplier - higher volatility = wider spreads
        if atr_pct > 5.0:
            spread_bps *= 3
        elif atr_pct > 3.0:
            spread_bps *= 2
        elif atr_pct > 2.0:
            spread_bps *= 1.5

        # Max acceptable spread
        max_spread = 30 if self.config.conservative_execution else 50

        return spread_bps <= max_spread

    def _check_exit(
        self,
        pos: BacktestTrade,
        current_time: datetime,
        high: float,
        low: float,
        close: float,
    ) -> Optional[str]:
        """
        Check exit conditions for open position.

        Args:
            pos: Open position
            current_time: Current candle time
            high: Current candle high
            low: Current candle low
            close: Current candle close

        Returns:
            Exit reason string or None
        """
        cfg = self.current_regime_config

        # Current P&L
        pnl_pct = ((close - pos.entry_price) / pos.entry_price) * 100

        # Check against low of bar (worst case for LONG)
        low_pnl = ((low - pos.entry_price) / pos.entry_price) * 100

        # =====================================================================
        # 1. HARD STOP LOSS
        # =====================================================================

        if low_pnl <= cfg.hard_sl_pct:
            return "HARD_SL"

        # =====================================================================
        # 2. BREAKEVEN ACTIVATION
        # =====================================================================

        if pos.highest_pnl_pct >= cfg.be_activation_pct and not pos.trailing_active:
            pos.trailing_active = True
            pos.current_sl_pct = 0.0  # Move SL to breakeven

        # =====================================================================
        # 3. TRAILING STOP UPDATE
        # =====================================================================

        if pos.trailing_active and pos.highest_pnl_pct > cfg.be_activation_pct:
            # Trail the stop
            new_sl = max(0.0, pos.highest_pnl_pct - cfg.trail_distance_pct)

            # Only move SL up, never down
            if new_sl > pos.current_sl_pct + 0.05:
                pos.current_sl_pct = new_sl

        # =====================================================================
        # 4. TRAILING STOP HIT
        # =====================================================================

        if pos.trailing_active and pnl_pct <= pos.current_sl_pct:
            return f"TRAIL_SL_{pos.current_sl_pct:+.1f}%"

        # =====================================================================
        # 5. TIMEOUT
        # =====================================================================

        hold_min = (current_time - pos.entry_time).total_seconds() / 60

        if hold_min >= cfg.timeout_minutes:
            if pnl_pct >= cfg.min_profit_to_close_pct:
                return "TIMEOUT_PROFIT"

            if pos.highest_pnl_pct > 0.3 and pnl_pct < 0.1:
                return "TIMEOUT_MISSED"

            if pos.highest_pnl_pct < 0.2:
                return "TIMEOUT_NO_MOMENTUM"

            if hold_min >= cfg.timeout_minutes + 30:
                return "TIMEOUT_EXTENDED"

        return None

    def _print_summary(self, result: BacktestResult):
        """Print results summary to console."""
        fee = self.config.total_fee_pct

        print(f"\n{'═' * 70}")
        print(f"  RESULTS: {result.period_name}")
        print(f"{'═' * 70}")

        # Capital
        print(f"\n  💰 Capital:")
        print(f"     Initial:       ${result.initial_balance:,.2f}")
        print(f"     Final:         ${result.final_balance:,.2f}")

        pnl = result.total_pnl_pct()
        pnl_color = "\033[92m" if pnl > 0 else "\033[91m"
        reset = "\033[0m"

        print(
            f"     P&L:           {pnl_color}{pnl:+.2f}%{reset} (${result.total_pnl_usd():+,.2f})"
        )
        print(f"     Max Drawdown:  {result.max_drawdown_pct():.2f}%")

        # Trades
        print(f"\n  📊 Trades:")
        print(f"     Total:         {result.total_trades()}")
        print(
            f"     Wins:          {result.winning_trades()} ({result.winrate():.1f}%)"
        )
        print(f"     Losses:        {result.losing_trades()}")
        print(f"     Profit Factor: {result.profit_factor():.2f}")

        # Performance
        print(f"\n  📈 Performance:")
        print(f"     Avg Trade:     {result.avg_trade_pnl():.2f}%")
        print(f"     Best Trade:    {result.best_trade():.2f}%")
        print(f"     Worst Trade:   {result.worst_trade():.2f}%")
        print(f"     Avg Hold:      {result.avg_hold_time():.0f} min")
        print(f"     Sharpe:        {result.sharpe_ratio():.2f}")
        print(f"     Sortino:       {result.sortino_ratio():.2f}")
        print(f"     Expectancy:    {result.expectancy():.2f}%")

        # Signals
        print(f"\n  📡 Signals:")
        print(f"     Generated:     {result.signals_generated}")
        print(f"     Executed:      {result.signals_executed}")
        print(
            f"     Rejected:      {result.signals_rejected_score + result.signals_rejected_spread}"
        )

        # Costs
        if result.total_slippage_cost > 0 or result.total_spread_cost > 0:
            print(f"\n  💸 Costs:")
            print(f"     Slippage:      ${result.total_slippage_cost:.2f}")
            print(f"     Spread:        ${result.total_spread_cost:.2f}")

        # Regime breakdown
        if self.config.adaptive_regime and result.regime_distribution:
            print(f"\n  🌍 Regime Distribution:")
            total = sum(result.regime_distribution.values())

            for regime, candles in sorted(result.regime_distribution.items()):
                pct = (candles / total * 100) if total > 0 else 0
                print(f"     {regime.upper():<8} {pct:5.1f}%")

            # Performance by regime
            regime_perf = result.performance_by_regime()

            if regime_perf:
                print(f"\n  📊 Performance by Regime:")

                for regime in ["bull", "bear", "range", "crash"]:
                    if regime in regime_perf:
                        stats = regime_perf[regime]
                        regime_pnl = stats["total_pnl"]
                        color = "\033[92m" if regime_pnl > 0 else "\033[91m"

                        print(
                            f"     {regime.upper():<8} {stats['trades']:3d} trades | "
                            f"WR: {stats['winrate']:5.1f}% | "
                            f"{color}{regime_pnl:+.2f}%{reset}"
                        )

        print(f"\n  ⏱️  Completed in {result.backtest_duration_sec:.1f}s")
        print(f"{'═' * 70}\n")
