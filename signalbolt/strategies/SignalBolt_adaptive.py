"""
SignalBolt Adaptive Strategy

Meta-strategy that automatically switches behavior based on market regime.

Philosophy:
    "The market changes, so should we. No single strategy works in all conditions."

How It Works:
    1. Detects current market regime (BULL/BEAR/RANGE/CRASH)
    2. Selects optimal strategy variant for that regime
    3. Delegates signal generation to selected variant
    4. Adjusts parameters dynamically based on regime transitions
    5. Tracks regime history and strategy performance per regime

Regime-to-Strategy Mapping (default):
    BULL      -> SignalBoltAggressive    (maximize opportunities)
    RANGE     -> SignalBoltOriginal      (balanced approach)
    BEAR      -> SignalBoltConservative  (capital preservation)
    CRASH     -> SignalBoltConservative  (extreme defense)

    Or optionally:
    BULL      -> SignalBoltScalper       (fast-paced bull scalping)
    RANGE     -> SignalBoltScalper       (range-bound scalping)
    BEAR      -> SignalBoltConservative  (wait for clarity)
    CRASH     -> DISABLED                (sit out)

Key Features:
    - Automatic strategy switching (no manual intervention)
    - Regime history tracking (know when market changed)
    - Per-regime performance statistics
    - Smooth transitions (prevents thrashing)
    - Configurable hysteresis (regime must persist N scans before switch)
    - Custom regime-to-strategy mappings
    - Fallback strategy for uncertain regimes
    - Detailed logging of regime changes

Configuration Example:
    strategy:
      name: "SignalBoltAdaptive"

      # Regime detection
      adaptive_regime_update_interval: 300    # Check regime every 5 min
      adaptive_regime_hysteresis: 3           # Require 3 confirmations before switch

      # Strategy mapping
      adaptive_bull_strategy: "SignalBoltAggressive"
      adaptive_range_strategy: "SignalBoltOriginal"
      adaptive_bear_strategy: "SignalBoltConservative"
      adaptive_crash_strategy: "SignalBoltConservative"  # or "DISABLED"

      # Fallback
      adaptive_fallback_strategy: "SignalBoltOriginal"

      # Transition smoothing
      adaptive_smooth_transitions: true       # Gradual parameter adjustment
      adaptive_transition_steps: 5            # Steps for parameter interpolation

Performance Targets:
    - Win rate: 55-70% (varies by regime)
    - Avg profit per trade: 0.4-1.2%
    - Sharpe ratio: >1.8 (higher than any single variant)
    - Max drawdown: <8% (better risk management)
    - Adaptability: responds within 15-30 minutes to regime changes

Advanced Features:
    - Regime prediction (ML-based, future)
    - Multi-symbol regime correlation
    - Regime-based portfolio rebalancing
    - Strategy performance benchmarking per regime
"""

from typing import Optional, Dict, List, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum
import pandas as pd
import json

from signalbolt.core.strategy import Strategy, Signal, EntryPlan, ExitPlan
from signalbolt.core.config import Config
from signalbolt.core.indicators import IndicatorValues
from signalbolt.exchange.base import Ticker
from signalbolt.regime.detector import RegimeDetector, MarketRegime
from signalbolt.regime.presets import get_regime_preset
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.strategies.adaptive")


# =============================================================================
# REGIME HISTORY TRACKING
# =============================================================================


@dataclass
class RegimeTransition:
    """Record of a regime change."""

    timestamp: datetime
    from_regime: MarketRegime
    to_regime: MarketRegime
    from_strategy: str
    to_strategy: str
    trigger: str  # "detection" / "manual" / "forced"
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "from_regime": self.from_regime.value,
            "to_regime": self.to_regime.value,
            "from_strategy": self.from_strategy,
            "to_strategy": self.to_strategy,
            "trigger": self.trigger,
            "confidence": self.confidence,
        }


@dataclass
class RegimeStats:
    """Performance statistics per regime."""

    regime: MarketRegime
    strategy_used: str
    total_signals: int = 0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    avg_pnl_per_trade: float = 0.0
    win_rate: float = 0.0
    time_in_regime_minutes: float = 0.0
    last_active: Optional[datetime] = None

    def update_trade(self, pnl: float):
        """Update stats with a completed trade."""
        self.total_trades += 1
        self.total_pnl += pnl

        if pnl > 0:
            self.winning_trades += 1
        elif pnl < 0:
            self.losing_trades += 1

        if self.total_trades > 0:
            self.avg_pnl_per_trade = self.total_pnl / self.total_trades
            self.win_rate = (self.winning_trades / self.total_trades) * 100

        self.last_active = datetime.now()

    def to_dict(self) -> dict:
        return {
            "regime": self.regime.value,
            "strategy_used": self.strategy_used,
            "total_signals": self.total_signals,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "total_pnl": round(self.total_pnl, 2),
            "avg_pnl_per_trade": round(self.avg_pnl_per_trade, 4),
            "win_rate": round(self.win_rate, 2),
            "time_in_regime_minutes": round(self.time_in_regime_minutes, 1),
            "last_active": self.last_active.isoformat() if self.last_active else None,
        }


# =============================================================================
# ADAPTIVE STRATEGY STATE
# =============================================================================


@dataclass
class AdaptiveState:
    """Current state of the adaptive strategy."""

    current_regime: MarketRegime = MarketRegime.RANGE
    current_strategy_name: str = "SignalBoltOriginal"
    last_regime_check: datetime = field(default_factory=datetime.now)
    regime_confirmation_count: int = 0  # Hysteresis counter
    pending_regime: Optional[MarketRegime] = None

    # History
    transitions: List[RegimeTransition] = field(default_factory=list)
    regime_stats: Dict[MarketRegime, RegimeStats] = field(default_factory=dict)

    # Session tracking
    regime_enter_time: datetime = field(default_factory=datetime.now)
    total_regime_changes: int = 0

    def to_dict(self) -> dict:
        return {
            "current_regime": self.current_regime.value,
            "current_strategy_name": self.current_strategy_name,
            "last_regime_check": self.last_regime_check.isoformat(),
            "regime_confirmation_count": self.regime_confirmation_count,
            "pending_regime": self.pending_regime.value
            if self.pending_regime
            else None,
            "total_regime_changes": self.total_regime_changes,
            "regime_enter_time": self.regime_enter_time.isoformat(),
            "transitions": [t.to_dict() for t in self.transitions[-10:]],  # Last 10
            "regime_stats": {
                regime.value: stats.to_dict()
                for regime, stats in self.regime_stats.items()
            },
        }

    def save(self, filepath: str):
        """Save state to JSON."""
        with open(filepath, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, filepath: str) -> "AdaptiveState":
        """Load state from JSON."""
        try:
            with open(filepath, "r") as f:
                data = json.load(f)

            state = cls()
            state.current_regime = MarketRegime(data["current_regime"])
            state.current_strategy_name = data["current_strategy_name"]
            state.total_regime_changes = data.get("total_regime_changes", 0)

            # Restore regime stats
            for regime_str, stats_dict in data.get("regime_stats", {}).items():
                regime = MarketRegime(regime_str)
                stats = RegimeStats(
                    regime=regime,
                    strategy_used=stats_dict["strategy_used"],
                    total_signals=stats_dict["total_signals"],
                    total_trades=stats_dict["total_trades"],
                    winning_trades=stats_dict["winning_trades"],
                    losing_trades=stats_dict["losing_trades"],
                    total_pnl=stats_dict["total_pnl"],
                    avg_pnl_per_trade=stats_dict["avg_pnl_per_trade"],
                    win_rate=stats_dict["win_rate"],
                    time_in_regime_minutes=stats_dict["time_in_regime_minutes"],
                )
                state.regime_stats[regime] = stats

            log.info(
                f"Adaptive state loaded: {state.current_regime.value} -> {state.current_strategy_name}"
            )
            return state

        except Exception as e:
            log.warning(f"Failed to load adaptive state: {e}, using defaults")
            return cls()


# =============================================================================
# DEFAULT REGIME-TO-STRATEGY MAPPING
# =============================================================================

DEFAULT_REGIME_STRATEGY_MAP = {
    MarketRegime.BULL: "SignalBoltAggressive",
    MarketRegime.RANGE: "SignalBoltOriginal",
    MarketRegime.BEAR: "SignalBoltConservative",
    MarketRegime.CRASH: "SignalBoltConservative",  # or "DISABLED"
}

# Alternative mapping (scalper-focused)
SCALPER_REGIME_STRATEGY_MAP = {
    MarketRegime.BULL: "SignalBoltScalper",
    MarketRegime.RANGE: "SignalBoltScalper",
    MarketRegime.BEAR: "SignalBoltConservative",
    MarketRegime.CRASH: "DISABLED",
}


# =============================================================================
# MAIN ADAPTIVE STRATEGY
# =============================================================================


class SignalBoltAdaptive(Strategy):
    """
    Meta-strategy that adapts to market regime.

    Automatically switches between strategy variants based on detected
    market conditions. Tracks performance per regime and manages smooth
    transitions to prevent thrashing.

    This is NOT a standalone strategy - it's an orchestrator that delegates
    to the appropriate variant (Original/Scalper/Conservative/Aggressive).
    """

    def __init__(self, config: Config):
        """Initialize adaptive strategy."""
        super().__init__(config)

        self.regime_detector = RegimeDetector()

        # ----- Configuration -----

        # Regime detection
        self.regime_update_interval_sec = config.get(
            "strategy", "adaptive_regime_update_interval", default=300
        )
        self.regime_hysteresis = config.get(
            "strategy", "adaptive_regime_hysteresis", default=3
        )

        # Strategy mapping (regime -> strategy name)
        self.strategy_map = {
            MarketRegime.BULL: config.get(
                "strategy",
                "adaptive_bull_strategy",
                default=DEFAULT_REGIME_STRATEGY_MAP[MarketRegime.BULL],
            ),
            MarketRegime.RANGE: config.get(
                "strategy",
                "adaptive_range_strategy",
                default=DEFAULT_REGIME_STRATEGY_MAP[MarketRegime.RANGE],
            ),
            MarketRegime.BEAR: config.get(
                "strategy",
                "adaptive_bear_strategy",
                default=DEFAULT_REGIME_STRATEGY_MAP[MarketRegime.BEAR],
            ),
            MarketRegime.CRASH: config.get(
                "strategy",
                "adaptive_crash_strategy",
                default=DEFAULT_REGIME_STRATEGY_MAP[MarketRegime.CRASH],
            ),
        }

        # Fallback strategy
        self.fallback_strategy_name = config.get(
            "strategy", "adaptive_fallback_strategy", default="SignalBoltOriginal"
        )

        # Transition smoothing
        self.smooth_transitions = config.get(
            "strategy", "adaptive_smooth_transitions", default=False
        )
        self.transition_steps = config.get(
            "strategy", "adaptive_transition_steps", default=5
        )

        # State persistence
        self.state_file = config.get(
            "strategy", "adaptive_state_file", default="data/adaptive_state.json"
        )

        # ----- Initialize state -----
        self.state = AdaptiveState.load(self.state_file)

        # Initialize regime stats if missing
        for regime in MarketRegime:
            if regime not in self.state.regime_stats:
                self.state.regime_stats[regime] = RegimeStats(
                    regime=regime,
                    strategy_used=self.strategy_map.get(
                        regime, self.fallback_strategy_name
                    ),
                )

        # ----- Create strategy instances -----
        self.strategies: Dict[str, Strategy] = {}

        # Get unique strategy names from mapping
        unique_strategies = set(self.strategy_map.values())
        unique_strategies.add(self.fallback_strategy_name)
        unique_strategies.discard("DISABLED")  # Remove DISABLED placeholder

        for strategy_name in unique_strategies:
            try:
                self.strategies[strategy_name] = self._create_strategy_instance(
                    strategy_name, config
                )
                log.info(f"✓ Initialized strategy variant: {strategy_name}")
            except Exception as e:
                log.error(f"Failed to initialize {strategy_name}: {e}")

        # ----- Current active strategy -----
        self.active_strategy = self._get_active_strategy()

        log.info(
            f"SignalBolt Adaptive initialized\n"
            f"  Current Regime: {self.state.current_regime.value}\n"
            f"  Active Strategy: {self.state.current_strategy_name}\n"
            f"  Regime Mapping: {self._format_strategy_map()}\n"
            f"  Hysteresis: {self.regime_hysteresis} confirmations\n"
            f"  Update Interval: {self.regime_update_interval_sec}s\n"
            f"  Smooth Transitions: {'ON' if self.smooth_transitions else 'OFF'}"
        )

    # =========================================================================
    # CORE: SIGNAL GENERATION
    # =========================================================================

    def generate_signal(
        self,
        df: pd.DataFrame,
        symbol: str,
        current_price: Optional[float] = None,
    ) -> Optional[Signal]:
        """
        Generate signal using adaptive strategy.

        Pipeline:
            1. Check if regime update is needed
            2. Update regime if interval elapsed
            3. Switch strategy if regime changed (with hysteresis)
            4. Delegate to active strategy
            5. Track signal in regime stats
            6. Return signal with adaptive metadata
        """

        # 1. Check if regime update needed
        now = datetime.now()
        time_since_check = (now - self.state.last_regime_check).total_seconds()

        if time_since_check >= self.regime_update_interval_sec:
            self._update_regime(df)

        # 2. Get active strategy
        active_strategy = self._get_active_strategy()

        if active_strategy is None:
            log.warning(
                f"No active strategy for regime {self.state.current_regime.value} "
                f"(mapped to {self.strategy_map.get(self.state.current_regime, 'UNKNOWN')})"
            )
            return None

        # 3. Delegate to active strategy
        signal = active_strategy.generate_signal(df, symbol, current_price)

        if signal is None:
            return None

        # 4. Augment signal with adaptive metadata
        signal.strategy_name = self.name  # Override to show "Adaptive"
        signal.notes = f"[{active_strategy.name}] {signal.notes}"

        # 5. Track in regime stats
        regime_stats = self.state.regime_stats[self.state.current_regime]
        regime_stats.total_signals += 1

        log.info(
            f"🔄 ADAPTIVE SIGNAL: {symbol} {signal.direction} "
            f"regime={self.state.current_regime.value} "
            f"via {active_strategy.name} "
            f"score={signal.score:.1f}"
        )

        return signal

    def calculate_entry(
        self, signal: Signal, ticker: Ticker, wallet_balance: float
    ) -> EntryPlan:
        """Delegate entry calculation to active strategy."""
        active_strategy = self._get_active_strategy()

        if active_strategy is None:
            raise ValueError("No active strategy available for entry calculation")

        plan = active_strategy.calculate_entry(signal, ticker, wallet_balance)

        # Augment with adaptive info
        plan.notes = f"[{active_strategy.name}] {plan.notes}"

        return plan

    def calculate_exits(
        self, entry_price: float, direction: str, indicators: IndicatorValues
    ) -> ExitPlan:
        """Delegate exit calculation to active strategy."""
        active_strategy = self._get_active_strategy()

        if active_strategy is None:
            raise ValueError("No active strategy available for exit calculation")

        return active_strategy.calculate_exits(entry_price, direction, indicators)

    # =========================================================================
    # REGIME MANAGEMENT
    # =========================================================================

    def _update_regime(self, df: pd.DataFrame):
        """
        Update current regime with hysteresis.

        Hysteresis prevents thrashing:
        - New regime must be detected N consecutive times before switch
        - Resets counter if regime flips back
        """
        detected_regime = self.regime_detector.detect(df)

        log.debug(
            f"Regime check: detected={detected_regime.value}, "
            f"current={self.state.current_regime.value}, "
            f"pending={self.state.pending_regime.value if self.state.pending_regime else 'None'}, "
            f"count={self.state.regime_confirmation_count}/{self.regime_hysteresis}"
        )

        # Update last check time
        self.state.last_regime_check = datetime.now()

        # Same as current - reset pending
        if detected_regime == self.state.current_regime:
            if self.state.pending_regime is not None:
                log.debug(
                    f"Regime reverted to {detected_regime.value}, resetting pending"
                )
            self.state.pending_regime = None
            self.state.regime_confirmation_count = 0
            return

        # New regime detected
        if self.state.pending_regime != detected_regime:
            # Different from pending - start new confirmation sequence
            log.debug(
                f"New regime detected: {detected_regime.value}, "
                f"starting confirmation (1/{self.regime_hysteresis})"
            )
            self.state.pending_regime = detected_regime
            self.state.regime_confirmation_count = 1
        else:
            # Same as pending - increment counter
            self.state.regime_confirmation_count += 1
            log.debug(
                f"Regime confirmation: {detected_regime.value} "
                f"({self.state.regime_confirmation_count}/{self.regime_hysteresis})"
            )

            # Threshold reached - switch regime
            if self.state.regime_confirmation_count >= self.regime_hysteresis:
                self._switch_regime(detected_regime)

    def _switch_regime(self, new_regime: MarketRegime):
        """
        Execute regime switch.

        Updates state, switches strategy, logs transition, saves state.
        """
        old_regime = self.state.current_regime
        old_strategy = self.state.current_strategy_name
        new_strategy = self.strategy_map.get(new_regime, self.fallback_strategy_name)

        # Update time in old regime
        time_in_regime = (
            datetime.now() - self.state.regime_enter_time
        ).total_seconds() / 60
        if old_regime in self.state.regime_stats:
            self.state.regime_stats[old_regime].time_in_regime_minutes += time_in_regime

        # Record transition
        transition = RegimeTransition(
            timestamp=datetime.now(),
            from_regime=old_regime,
            to_regime=new_regime,
            from_strategy=old_strategy,
            to_strategy=new_strategy,
            trigger="detection",
            confidence=1.0,
        )
        self.state.transitions.append(transition)

        # Update state
        self.state.current_regime = new_regime
        self.state.current_strategy_name = new_strategy
        self.state.regime_enter_time = datetime.now()
        self.state.total_regime_changes += 1
        self.state.pending_regime = None
        self.state.regime_confirmation_count = 0

        # Update active strategy
        self.active_strategy = self._get_active_strategy()

        # Save state
        try:
            self.state.save(self.state_file)
        except Exception as e:
            log.error(f"Failed to save adaptive state: {e}")

        log.warning(
            f"🔄 REGIME SWITCH: {old_regime.value} -> {new_regime.value}\n"
            f"   Strategy: {old_strategy} -> {new_strategy}\n"
            f"   Time in {old_regime.value}: {time_in_regime:.1f} min\n"
            f"   Total regime changes: {self.state.total_regime_changes}"
        )

    # =========================================================================
    # STRATEGY MANAGEMENT
    # =========================================================================

    def _get_active_strategy(self) -> Optional[Strategy]:
        """
        Get currently active strategy instance.

        Returns None if strategy is "DISABLED" (e.g., in CRASH mode).
        """
        strategy_name = self.state.current_strategy_name

        if strategy_name == "DISABLED":
            return None

        if strategy_name not in self.strategies:
            log.warning(
                f"Strategy {strategy_name} not initialized, "
                f"falling back to {self.fallback_strategy_name}"
            )
            strategy_name = self.fallback_strategy_name

        return self.strategies.get(strategy_name)

    def _create_strategy_instance(self, strategy_name: str, config: Config) -> Strategy:
        """
        Create strategy instance by name.

        Uses core factory to avoid circular imports.

        Args:
            strategy_name: Strategy name
            config: Config instance

        Returns:
            Strategy instance

        Raises:
            ValueError: If strategy unknown
        """
        from signalbolt.core.strategy import create_strategy

        return create_strategy(strategy_name, config)

    # =========================================================================
    # PERFORMANCE TRACKING
    # =========================================================================

    def record_trade(
        self,
        regime: MarketRegime,
        pnl: float,
        symbol: str,
        direction: str,
    ):
        """
        Record a completed trade for regime stats.

        Call this from PaperExecutor or LiveExecutor after trade closes.
        """
        if regime in self.state.regime_stats:
            self.state.regime_stats[regime].update_trade(pnl)

            log.info(
                f"📊 Trade recorded: {symbol} {direction} "
                f"PnL=${pnl:.2f} in {regime.value} regime"
            )

            # Save updated stats
            try:
                self.state.save(self.state_file)
            except Exception as e:
                log.error(f"Failed to save state after trade: {e}")

    def get_regime_stats(self, regime: Optional[MarketRegime] = None) -> Dict[str, any]:
        """
        Get performance statistics.

        Args:
            regime: Specific regime, or None for all

        Returns:
            Dict with statistics
        """
        if regime is not None:
            if regime in self.state.regime_stats:
                return self.state.regime_stats[regime].to_dict()
            return {}

        return {
            regime.value: stats.to_dict()
            for regime, stats in self.state.regime_stats.items()
        }

    def get_transition_history(self, limit: int = 10) -> List[Dict]:
        """Get recent regime transitions."""
        return [t.to_dict() for t in self.state.transitions[-limit:]]

    # =========================================================================
    # MANUAL CONTROL
    # =========================================================================

    def force_regime(self, regime: MarketRegime, reason: str = "manual"):
        """
        Manually force a regime switch.

        Use with caution - bypasses hysteresis.
        """
        old_regime = self.state.current_regime
        old_strategy = self.state.current_strategy_name
        new_strategy = self.strategy_map.get(regime, self.fallback_strategy_name)

        transition = RegimeTransition(
            timestamp=datetime.now(),
            from_regime=old_regime,
            to_regime=regime,
            from_strategy=old_strategy,
            to_strategy=new_strategy,
            trigger=reason,
            confidence=0.0,
        )
        self.state.transitions.append(transition)

        self.state.current_regime = regime
        self.state.current_strategy_name = new_strategy
        self.state.regime_enter_time = datetime.now()
        self.state.pending_regime = None
        self.state.regime_confirmation_count = 0

        self.active_strategy = self._get_active_strategy()

        log.warning(
            f"⚠️ FORCED REGIME SWITCH: {old_regime.value} -> {regime.value} "
            f"(reason: {reason})"
        )

        try:
            self.state.save(self.state_file)
        except Exception as e:
            log.error(f"Failed to save state: {e}")

    # =========================================================================
    # UTILITIES
    # =========================================================================

    def _format_strategy_map(self) -> str:
        """Format strategy mapping for logging."""
        lines = []
        for regime, strategy in self.strategy_map.items():
            marker = "→" if regime == self.state.current_regime else " "
            lines.append(f"{marker} {regime.value:8} -> {strategy}")
        return "\n  ".join(lines)

    def get_current_regime_info(self) -> Dict:
        """Get detailed info about current regime."""
        time_in_regime = (
            datetime.now() - self.state.regime_enter_time
        ).total_seconds() / 60

        stats = self.state.regime_stats.get(self.state.current_regime)

        return {
            "regime": self.state.current_regime.value,
            "strategy": self.state.current_strategy_name,
            "time_in_regime_minutes": round(time_in_regime, 1),
            "total_regime_changes": self.state.total_regime_changes,
            "stats": stats.to_dict() if stats else None,
            "pending_regime": (
                self.state.pending_regime.value if self.state.pending_regime else None
            ),
            "confirmation_progress": (
                f"{self.state.regime_confirmation_count}/{self.regime_hysteresis}"
            ),
        }

    # =========================================================================
    # STRATEGY INTERFACE COMPLIANCE
    # =========================================================================

    def get_min_data_length(self) -> int:
        """Minimum candles needed (max of all variants)."""
        if not self.strategies:
            return 100

        return max(
            strategy.get_min_data_length() for strategy in self.strategies.values()
        )

    def supports_timeframe(self, timeframe: str) -> bool:
        """Check if ANY strategy variant supports this timeframe."""
        return any(
            strategy.supports_timeframe(timeframe)
            for strategy in self.strategies.values()
        )

    def get_optimal_timeframes(self) -> list[str]:
        """Get union of optimal timeframes from all variants."""
        all_timeframes = set()
        for strategy in self.strategies.values():
            all_timeframes.update(strategy.get_optimal_timeframes())
        return sorted(all_timeframes)

    def get_display_name(self) -> str:
        """Get display name including current active strategy."""
        return f"SignalBoltAdaptive[{self.state.current_strategy_name}]"

    def __repr__(self) -> str:
        return (
            f"SignalBoltAdaptive("
            f"regime={self.state.current_regime.value}, "
            f"strategy={self.state.current_strategy_name}, "
            f"changes={self.state.total_regime_changes}, "
            f"hysteresis={self.regime_hysteresis})"
        )


# =============================================================================
# CLI HELPER: REGIME STATUS DISPLAY
# =============================================================================


def print_adaptive_status(adaptive: SignalBoltAdaptive):
    """
    Print detailed adaptive strategy status.

    Use in CLI menu or monitoring dashboard.
    """
    info = adaptive.get_current_regime_info()

    print("\n" + "=" * 80)
    print("🔄 ADAPTIVE STRATEGY STATUS")
    print("=" * 80)

    # Current regime
    regime_emoji = {
        "BULL": "🟢",
        "RANGE": "🟡",
        "BEAR": "🔴",
        "CRASH": "⚠️",
    }
    emoji = regime_emoji.get(info["regime"], "❓")

    print(f"\n{emoji} Current Regime: {info['regime']}")
    print(f"   Active Strategy: {info['strategy']}")
    print(f"   Time in Regime: {info['time_in_regime_minutes']:.1f} min")
    print(f"   Total Regime Changes: {info['total_regime_changes']}")

    # Pending regime
    if info["pending_regime"]:
        print(f"\n⏳ Pending Regime Change: {info['pending_regime']}")
        print(f"   Confirmation: {info['confirmation_progress']}")

    # Regime stats
    if info["stats"]:
        stats = info["stats"]
        print(f"\n📊 {info['regime']} Regime Statistics:")
        print(f"   Signals: {stats['total_signals']}")
        print(f"   Trades: {stats['total_trades']}")
        if stats["total_trades"] > 0:
            print(f"   Win Rate: {stats['win_rate']:.1f}%")
            print(f"   Avg PnL/Trade: ${stats['avg_pnl_per_trade']:.4f}")
            print(f"   Total PnL: ${stats['total_pnl']:.2f}")
        print(f"   Time in Regime: {stats['time_in_regime_minutes']:.1f} min")

    # Recent transitions
    transitions = adaptive.get_transition_history(limit=5)
    if transitions:
        print(f"\n📜 Recent Regime Changes (last 5):")
        for trans in reversed(transitions):
            ts = datetime.fromisoformat(trans["timestamp"])
            print(
                f"   {ts.strftime('%H:%M:%S')}: "
                f"{trans['from_regime']} -> {trans['to_regime']} "
                f"({trans['from_strategy']} -> {trans['to_strategy']})"
            )

    # Strategy mapping
    print(f"\n🗺️  Regime-to-Strategy Mapping:")
    for regime, strategy in adaptive.strategy_map.items():
        marker = "→" if regime.value == info["regime"] else " "
        print(f"   {marker} {regime.value:8} -> {strategy}")

    print("=" * 80 + "\n")


# =============================================================================
# EXAMPLE CONFIGURATION
# =============================================================================

EXAMPLE_CONFIG_YAML = """
# Adaptive strategy configuration example

strategy:
  name: "SignalBoltAdaptive"
  
  # -------------------------------------------------------------------------
  # REGIME DETECTION
  # -------------------------------------------------------------------------
  
  # How often to check for regime changes (seconds)
  adaptive_regime_update_interval: 300  # 5 minutes
  
  # Hysteresis: regime must be detected N times before switching
  # Prevents thrashing on regime boundaries
  adaptive_regime_hysteresis: 3
  
  # -------------------------------------------------------------------------
  # STRATEGY MAPPING
  # -------------------------------------------------------------------------
  
  # Which strategy to use for each regime
  adaptive_bull_strategy: "SignalBoltAggressive"    # Maximize opportunities
  adaptive_range_strategy: "SignalBoltOriginal"      # Balanced approach
  adaptive_bear_strategy: "SignalBoltConservative"   # Capital preservation
  adaptive_crash_strategy: "SignalBoltConservative"  # Extreme defense
  # Or: adaptive_crash_strategy: "DISABLED"          # Sit out crashes
  
  # Fallback strategy (if mapping fails)
  adaptive_fallback_strategy: "SignalBoltOriginal"
  
  # -------------------------------------------------------------------------
  # ALTERNATIVE MAPPING: SCALPER-FOCUSED
  # -------------------------------------------------------------------------
  # Uncomment to use scalper-based adaptation
  
  # adaptive_bull_strategy: "SignalBoltScalper"
  # adaptive_range_strategy: "SignalBoltScalper"
  # adaptive_bear_strategy: "SignalBoltConservative"
  # adaptive_crash_strategy: "DISABLED"
  
  # -------------------------------------------------------------------------
  # TRANSITION SMOOTHING (Advanced)
  # -------------------------------------------------------------------------
  
  # Smooth parameter transitions between strategies
  adaptive_smooth_transitions: false  # Enable gradual adjustment
  adaptive_transition_steps: 5        # Steps for parameter interpolation
  
  # -------------------------------------------------------------------------
  # STATE PERSISTENCE
  # -------------------------------------------------------------------------
  
  # Where to save adaptive state (JSON)
  adaptive_state_file: "data/adaptive_state.json"

# -------------------------------------------------------------------------
# REGIME DETECTION SETTINGS (from regime/detector.py)
# -------------------------------------------------------------------------

regime:
  enabled: true
  lookback_days: 45
  
  # Thresholds (BTC change %)
  bull_threshold_pct: 25.0
  bear_threshold_pct: -15.0
  crash_threshold_pct: -25.0
  
  # Update frequency
  update_interval_hours: 4
  
  reference_symbol: "BTCUSDT"
"""


# =============================================================================
# INTEGRATION NOTES
# =============================================================================

"""
INTEGRATION WITH PAPER/LIVE TRADING:

1. PaperEngine/LiveEngine initialization:
   
   if config.get("strategy", "name") == "SignalBoltAdaptive":
       strategy = SignalBoltAdaptive(config)
   else:
       strategy = create_strategy(config.get("strategy", "name"), config)

2. After trade closes (in PaperExecutor/LiveExecutor):
   
   if isinstance(self.strategy, SignalBoltAdaptive):
       self.strategy.record_trade(
           regime=current_regime,
           pnl=position.realized_pnl,
           symbol=position.symbol,
           direction=position.direction,
       )

3. CLI menu integration:
   
   if isinstance(engine.strategy, SignalBoltAdaptive):
       # Show adaptive status
       print_adaptive_status(engine.strategy)
       
       # Allow manual regime override
       regime_input = input("Force regime (BULL/BEAR/RANGE/CRASH) or Enter to skip: ")
       if regime_input:
           engine.strategy.force_regime(
               MarketRegime(regime_input.upper()), 
               reason="user_override"
           )

4. Monitoring/Dashboard:
   
   status = adaptive_strategy.get_current_regime_info()
   transitions = adaptive_strategy.get_transition_history(limit=20)
   stats = adaptive_strategy.get_regime_stats()
   
   # Display in web dashboard, send to Telegram, etc.

5. Backtesting with Adaptive:
   
   - Backtest engine needs to support regime detection per bar
   - Track regime changes during backtest
   - Report per-regime performance
   - Compare adaptive vs. single-strategy benchmarks
"""

# =============================================================================
# FUTURE ENHANCEMENTS
# =============================================================================

"""
POSSIBLE FUTURE FEATURES:

1. ML-Based Regime Prediction:
   - Train classifier on price/volume/indicator features
   - Predict regime N bars ahead
   - Preemptive strategy switching

2. Multi-Symbol Regime Correlation:
   - Detect regime across multiple assets
   - Weight by correlation/volume
   - More robust regime detection

3. Adaptive Position Sizing:
   - Increase size in favorable regimes
   - Decrease in uncertain transitions

4. Strategy Performance Learning:
   - Track which strategy performs best per regime per symbol
   - Override default mapping with learned preferences

5. Hybrid Strategies:
   - Blend signals from multiple strategies
   - Weight by regime confidence

6. Real-Time Regime Confidence:
   - Output confidence score (0-100%)
   - Lower confidence = more conservative behavior

7. Event-Driven Regime Overrides:
   - News sentiment API integration
   - Force CRASH on high-impact negative news

8. Portfolio Regime Balancing:
   - Hold mix of strategies across regimes
   - Hedge with counter-regime positions
"""
