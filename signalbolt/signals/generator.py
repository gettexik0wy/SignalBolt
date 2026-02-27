"""
Signal generator - creates trading signals from market data.

Features:
- Uses Strategy base class for signal logic
- Multi-timeframe support (MTF)
- Batch signal generation for multiple coins
- Score-based ranking
- Filter integration
- Regime-aware generation
- Signal history tracking

Usage:
    generator = SignalGenerator(exchange, config, strategy)

    # Single symbol (uses config timeframe)
    signal = generator.generate('BTCUSDT')

    # Specific timeframe
    signal = generator.generate('BTCUSDT', timeframe='15m')

    # Multiple symbols
    signals = generator.generate_batch(['BTCUSDT', 'ETHUSDT', 'SOLUSDT'])

    # With ranking (best first)
    ranked = generator.generate_ranked(symbols, top_n=5)

    # Multi-timeframe signal
    signal = generator.generate_mtf('BTCUSDT', timeframes=['5m', '15m', '1h'])
"""

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple, Callable, Union
from dataclasses import dataclass, field
from collections import deque
import threading

from signalbolt.core.config import Config
from signalbolt.core.strategy import Strategy, Signal
from signalbolt.core.indicators import IndicatorCalculator, IndicatorValues
from signalbolt.core.scoring import ScoreBreakdown, calculate_score
from signalbolt.core.filters import SignalFilter, FilterChainResult
from signalbolt.exchange.base import ExchangeBase, Ticker
from signalbolt.data.manager import DataManager
from signalbolt.data.price_feed import PriceFeed
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.signals.generator")


# =============================================================================
# SUPPORTED TIMEFRAMES
# =============================================================================

VALID_TIMEFRAMES = [
    "1m",
    "3m",
    "5m",
    "15m",
    "30m",  # Minutes
    "1h",
    "2h",
    "4h",
    "6h",
    "8h",
    "12h",  # Hours
    "1d",
    "3d",  # Days
    "1w",
    "1M",  # Week/Month
]

# Timeframe hierarchy (lower index = faster timeframe)
TIMEFRAME_HIERARCHY = {
    "1m": 1,
    "3m": 3,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "6h": 360,
    "8h": 480,
    "12h": 720,
    "1d": 1440,
    "3d": 4320,
    "1w": 10080,
    "1M": 43200,
}

# Minimum candles required per timeframe
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


def validate_timeframe(timeframe: str) -> bool:
    """Check if timeframe is valid."""
    return timeframe in VALID_TIMEFRAMES


def get_timeframe_minutes(timeframe: str) -> int:
    """Get timeframe duration in minutes."""
    return TIMEFRAME_HIERARCHY.get(timeframe, 5)


def compare_timeframes(tf1: str, tf2: str) -> int:
    """
    Compare two timeframes.
    Returns: -1 if tf1 < tf2, 0 if equal, 1 if tf1 > tf2
    """
    m1 = get_timeframe_minutes(tf1)
    m2 = get_timeframe_minutes(tf2)

    if m1 < m2:
        return -1
    elif m1 > m2:
        return 1
    return 0


# =============================================================================
# MTF SIGNAL RESULT
# =============================================================================


@dataclass
class MTFSignalResult:
    """Multi-timeframe signal analysis result."""

    symbol: str
    timestamp: datetime

    # Per-timeframe signals
    timeframe_signals: Dict[str, Optional[Signal]] = field(default_factory=dict)
    timeframe_directions: Dict[str, Optional[str]] = field(default_factory=dict)

    # Consensus
    primary_signal: Optional[Signal] = None
    consensus_direction: Optional[str] = None
    alignment_score: float = 0.0  # 0-100, how aligned are timeframes

    # Breakdown
    bullish_timeframes: int = 0
    bearish_timeframes: int = 0
    neutral_timeframes: int = 0

    # Flags
    is_aligned: bool = False
    conflicting: bool = False

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "consensus_direction": self.consensus_direction,
            "alignment_score": round(self.alignment_score, 1),
            "is_aligned": self.is_aligned,
            "conflicting": self.conflicting,
            "bullish_timeframes": self.bullish_timeframes,
            "bearish_timeframes": self.bearish_timeframes,
            "neutral_timeframes": self.neutral_timeframes,
            "timeframe_directions": self.timeframe_directions,
            "primary_signal": self.primary_signal.to_dict()
            if self.primary_signal
            else None,
        }


# =============================================================================
# GENERATION RESULT
# =============================================================================


@dataclass
class GenerationResult:
    """Result of signal generation attempt."""

    symbol: str
    timestamp: datetime
    timeframe: str = "5m"

    # Outcome
    signal: Optional[Signal] = None
    success: bool = False

    # Rejection reason (if no signal)
    rejection_reason: Optional[str] = None
    rejection_stage: Optional[str] = (
        None  # 'data', 'indicators', 'direction', 'score', 'filter'
    )

    # Raw data (for debugging)
    indicators: Optional[IndicatorValues] = None
    raw_score: float = 0.0
    filter_result: Optional[FilterChainResult] = None

    # Timing
    generation_time_ms: float = 0.0

    def to_dict(self) -> dict:
        result = {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "timeframe": self.timeframe,
            "success": self.success,
            "generation_time_ms": round(self.generation_time_ms, 2),
        }

        if self.signal:
            result["signal"] = self.signal.to_dict()

        if self.rejection_reason:
            result["rejection"] = {
                "reason": self.rejection_reason,
                "stage": self.rejection_stage,
            }

        if self.indicators:
            result["indicators"] = self.indicators.to_dict()

        return result


@dataclass
class BatchGenerationResult:
    """Result of batch signal generation."""

    timestamp: datetime
    timeframe: str = "5m"

    # Results
    results: List[GenerationResult] = field(default_factory=list)
    signals: List[Signal] = field(default_factory=list)  # Only successful signals

    # Stats
    total_symbols: int = 0
    successful: int = 0
    rejected: int = 0
    errors: int = 0

    # Timing
    total_time_ms: float = 0.0

    # Rejection breakdown
    rejection_reasons: Dict[str, int] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        """Percentage of symbols that generated signals."""
        if self.total_symbols == 0:
            return 0.0
        return (self.successful / self.total_symbols) * 100

    def get_top_signals(self, n: int = 5) -> List[Signal]:
        """Get top N signals by score."""
        sorted_signals = sorted(self.signals, key=lambda s: s.score, reverse=True)
        return sorted_signals[:n]

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "timeframe": self.timeframe,
            "total_symbols": self.total_symbols,
            "successful": self.successful,
            "rejected": self.rejected,
            "errors": self.errors,
            "success_rate": round(self.success_rate, 1),
            "total_time_ms": round(self.total_time_ms, 2),
            "rejection_reasons": self.rejection_reasons,
            "signals": [s.to_dict() for s in self.signals],
        }


# =============================================================================
# SIGNAL HISTORY
# =============================================================================


@dataclass
class SignalHistoryEntry:
    """Historical signal record."""

    signal: Signal
    generated_at: datetime
    timeframe: str = "5m"
    was_executed: bool = False
    execution_price: Optional[float] = None
    outcome: Optional[str] = None  # 'win', 'loss', 'pending'


class SignalHistory:
    """
    Track generated signals for analysis and cooldown.

    Features:
    - Recent signal tracking
    - Cooldown enforcement
    - Performance tracking
    - Per-timeframe history
    """

    def __init__(self, max_history: int = 1000):
        self._history: deque[SignalHistoryEntry] = deque(maxlen=max_history)
        self._by_symbol: Dict[str, List[SignalHistoryEntry]] = {}
        self._by_timeframe: Dict[str, List[SignalHistoryEntry]] = {}
        self._lock = threading.Lock()

    def add(self, signal: Signal, timeframe: str = "5m"):
        """Add signal to history."""
        entry = SignalHistoryEntry(
            signal=signal, generated_at=datetime.now(), timeframe=timeframe
        )

        with self._lock:
            self._history.append(entry)

            # By symbol
            if signal.symbol not in self._by_symbol:
                self._by_symbol[signal.symbol] = []
            self._by_symbol[signal.symbol].append(entry)

            # Trim per-symbol history
            if len(self._by_symbol[signal.symbol]) > 100:
                self._by_symbol[signal.symbol] = self._by_symbol[signal.symbol][-100:]

            # By timeframe
            if timeframe not in self._by_timeframe:
                self._by_timeframe[timeframe] = []
            self._by_timeframe[timeframe].append(entry)

            # Trim per-timeframe
            if len(self._by_timeframe[timeframe]) > 200:
                self._by_timeframe[timeframe] = self._by_timeframe[timeframe][-200:]

    def get_last_signal(
        self, symbol: str, timeframe: Optional[str] = None
    ) -> Optional[SignalHistoryEntry]:
        """Get last signal for symbol (optionally filtered by timeframe)."""
        with self._lock:
            if symbol not in self._by_symbol:
                return None

            entries = self._by_symbol[symbol]

            if timeframe:
                entries = [e for e in entries if e.timeframe == timeframe]

            return entries[-1] if entries else None

    def get_time_since_last(
        self, symbol: str, timeframe: Optional[str] = None
    ) -> Optional[timedelta]:
        """Get time since last signal for symbol."""
        last = self.get_last_signal(symbol, timeframe)

        if last:
            return datetime.now() - last.generated_at

        return None

    def is_in_cooldown(
        self, symbol: str, cooldown_minutes: int, timeframe: Optional[str] = None
    ) -> bool:
        """Check if symbol is in cooldown period."""
        time_since = self.get_time_since_last(symbol, timeframe)

        if time_since is None:
            return False

        return time_since < timedelta(minutes=cooldown_minutes)

    def get_recent(self, minutes: int = 60) -> List[SignalHistoryEntry]:
        """Get signals from last N minutes."""
        cutoff = datetime.now() - timedelta(minutes=minutes)

        with self._lock:
            return [e for e in self._history if e.generated_at >= cutoff]

    def get_recent_by_timeframe(
        self, timeframe: str, minutes: int = 60
    ) -> List[SignalHistoryEntry]:
        """Get signals for specific timeframe from last N minutes."""
        cutoff = datetime.now() - timedelta(minutes=minutes)

        with self._lock:
            if timeframe not in self._by_timeframe:
                return []

            return [
                e for e in self._by_timeframe[timeframe] if e.generated_at >= cutoff
            ]

    def get_symbol_history(
        self, symbol: str, limit: int = 20, timeframe: Optional[str] = None
    ) -> List[SignalHistoryEntry]:
        """Get history for specific symbol."""
        with self._lock:
            if symbol not in self._by_symbol:
                return []

            entries = self._by_symbol[symbol]

            if timeframe:
                entries = [e for e in entries if e.timeframe == timeframe]

            return entries[-limit:]

    def mark_executed(self, symbol: str, price: float):
        """Mark last signal as executed."""
        with self._lock:
            if symbol in self._by_symbol and self._by_symbol[symbol]:
                self._by_symbol[symbol][-1].was_executed = True
                self._by_symbol[symbol][-1].execution_price = price

    def mark_outcome(self, symbol: str, outcome: str):
        """Mark outcome of last executed signal."""
        with self._lock:
            if symbol in self._by_symbol:
                for entry in reversed(self._by_symbol[symbol]):
                    if entry.was_executed and entry.outcome is None:
                        entry.outcome = outcome
                        break

    def get_stats(self) -> dict:
        """Get history statistics."""
        with self._lock:
            total = len(self._history)
            executed = sum(1 for e in self._history if e.was_executed)

            wins = sum(1 for e in self._history if e.outcome == "win")
            losses = sum(1 for e in self._history if e.outcome == "loss")

            # Per-timeframe stats
            tf_stats = {}
            for tf, entries in self._by_timeframe.items():
                tf_stats[tf] = {
                    "total": len(entries),
                    "executed": sum(1 for e in entries if e.was_executed),
                }

            return {
                "total_signals": total,
                "executed": executed,
                "execution_rate": (executed / total * 100) if total > 0 else 0,
                "wins": wins,
                "losses": losses,
                "win_rate": (wins / (wins + losses) * 100)
                if (wins + losses) > 0
                else 0,
                "unique_symbols": len(self._by_symbol),
                "timeframe_breakdown": tf_stats,
            }

    def clear(self):
        """Clear all history."""
        with self._lock:
            self._history.clear()
            self._by_symbol.clear()
            self._by_timeframe.clear()


# =============================================================================
# SIGNAL GENERATOR
# =============================================================================


class SignalGenerator:
    """
    Generate trading signals using strategy and filters.

    Supports:
    - Single timeframe signals
    - Multi-timeframe (MTF) analysis
    - Configurable timeframes from config

    Usage:
        generator = SignalGenerator(exchange, config, strategy)

        # Single signal (uses default timeframe from config)
        signal = generator.generate('BTCUSDT')

        # Specific timeframe
        signal = generator.generate('BTCUSDT', timeframe='15m')

        # Multi-timeframe
        mtf_result = generator.generate_mtf('BTCUSDT', ['5m', '15m', '1h'])

        # Batch
        result = generator.generate_batch(symbols)

        # Ranked
        top_signals = generator.generate_ranked(symbols, top_n=3)
    """

    def __init__(
        self,
        exchange: ExchangeBase,
        config: Config,
        strategy: Strategy,
        data_manager: Optional[DataManager] = None,
        price_feed: Optional[PriceFeed] = None,
    ):
        """
        Initialize generator.

        Args:
            exchange: Exchange instance
            config: SignalBolt config
            strategy: Trading strategy
            data_manager: Data manager (creates if None)
            price_feed: Price feed (creates if None)
        """
        self.exchange = exchange
        self.config = config
        self.strategy = strategy

        # Components
        self.data_manager = data_manager or DataManager(mode="live", exchange=exchange)
        self.price_feed = price_feed or PriceFeed(exchange)
        self.indicator_calc = IndicatorCalculator()
        self.filter = SignalFilter(config)

        # History
        self.history = SignalHistory()

        # === TIMEFRAME SETTINGS (from config) ===
        self.default_timeframe = config.get("strategy", "timeframe", default="5m")
        self.mtf_timeframes = config.get(
            "strategy", "mtf_timeframes", default=["5m", "15m", "1h"]
        )
        self.mtf_enabled = config.get("strategy", "mtf_enabled", default=False)
        self.mtf_alignment_threshold = config.get(
            "strategy", "mtf_alignment_threshold", default=0.6
        )

        # Validate default timeframe
        if not validate_timeframe(self.default_timeframe):
            log.warning(
                f"Invalid timeframe '{self.default_timeframe}', falling back to '5m'"
            )
            self.default_timeframe = "5m"

        # === OTHER SETTINGS ===
        self.min_candles = config.get("generator", "min_candles", default=100)
        self.min_score = config.get("scanner", "min_signal_score", default=70)
        self.cooldown_minutes = config.get("scanner", "signal_cooldown_min", default=30)

        # Current regime (set externally)
        self.current_regime: str = "range"

        # Callbacks
        self._on_signal_callbacks: List[Callable[[Signal], None]] = []

        log.info(
            f"SignalGenerator initialized (strategy={strategy.name}, "
            f"timeframe={self.default_timeframe}, MTF={self.mtf_enabled}, "
            f"min_score={self.min_score})"
        )

    # =========================================================================
    # TIMEFRAME HELPERS
    # =========================================================================

    def get_min_candles(self, timeframe: str) -> int:
        """Get minimum candles required for timeframe."""
        return MIN_CANDLES_PER_TIMEFRAME.get(timeframe, self.min_candles)

    def set_timeframe(self, timeframe: str):
        """
        Set default timeframe.

        Args:
            timeframe: Valid timeframe string (1m, 5m, 15m, 1h, 4h, 1d, etc.)
        """
        if not validate_timeframe(timeframe):
            raise ValueError(
                f"Invalid timeframe: {timeframe}. Valid: {VALID_TIMEFRAMES}"
            )

        old_tf = self.default_timeframe
        self.default_timeframe = timeframe
        log.info(f"Default timeframe changed: {old_tf} → {timeframe}")

    def set_mtf_timeframes(self, timeframes: List[str]):
        """
        Set MTF timeframes.

        Args:
            timeframes: List of timeframes for MTF analysis
        """
        for tf in timeframes:
            if not validate_timeframe(tf):
                raise ValueError(f"Invalid timeframe: {tf}")

        # Sort by duration (fastest first)
        self.mtf_timeframes = sorted(timeframes, key=get_timeframe_minutes)
        log.info(f"MTF timeframes set: {self.mtf_timeframes}")

    def enable_mtf(self, enabled: bool = True):
        """Enable/disable MTF mode."""
        self.mtf_enabled = enabled
        log.info(f"MTF mode: {'enabled' if enabled else 'disabled'}")

    # =========================================================================
    # SINGLE SIGNAL GENERATION
    # =========================================================================

    def generate(
        self,
        symbol: str,
        timeframe: Optional[str] = None,
        check_cooldown: bool = True,
        current_positions: int = 0,
    ) -> Optional[Signal]:
        """
        Generate signal for single symbol.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe to use (None = default)
            check_cooldown: Check cooldown period
            current_positions: Number of open positions

        Returns:
            Signal if conditions met, None otherwise
        """
        result = self.generate_with_details(
            symbol,
            timeframe=timeframe,
            check_cooldown=check_cooldown,
            current_positions=current_positions,
        )
        return result.signal

    def generate_with_details(
        self,
        symbol: str,
        timeframe: Optional[str] = None,
        check_cooldown: bool = True,
        current_positions: int = 0,
    ) -> GenerationResult:
        """
        Generate signal with full details.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe (None = default from config)
            check_cooldown: Check cooldown
            current_positions: Open positions

        Returns:
            GenerationResult with signal or rejection reason
        """
        start_time = datetime.now()

        # Use default if not specified
        timeframe = timeframe or self.default_timeframe

        # Validate timeframe
        if not validate_timeframe(timeframe):
            return GenerationResult(
                symbol=symbol,
                timestamp=start_time,
                timeframe=timeframe,
                rejection_reason=f"Invalid timeframe: {timeframe}",
                rejection_stage="config",
            )

        result = GenerationResult(
            symbol=symbol, timestamp=start_time, timeframe=timeframe
        )

        try:
            # === COOLDOWN CHECK ===
            if check_cooldown and self.history.is_in_cooldown(
                symbol, self.cooldown_minutes, timeframe=timeframe
            ):
                result.rejection_reason = f"In cooldown ({self.cooldown_minutes}min)"
                result.rejection_stage = "cooldown"
                return self._finalize_result(result, start_time)

            # === FETCH DATA ===
            min_candles = self.get_min_candles(timeframe)

            df = self.data_manager.get_candles(
                symbol=symbol,
                interval=timeframe,
                limit=min_candles + 50,  # Extra buffer
            )

            if df is None or len(df) < min_candles:
                result.rejection_reason = f"Insufficient data ({len(df) if df is not None else 0}/{min_candles} candles)"
                result.rejection_stage = "data"
                return self._finalize_result(result, start_time)

            # === CALCULATE INDICATORS ===
            try:
                df_with_ind = self.indicator_calc.calculate(df, symbol)
                indicators = self.indicator_calc.get_latest(df_with_ind)
                result.indicators = indicators
            except Exception as e:
                result.rejection_reason = f"Indicator error: {str(e)}"
                result.rejection_stage = "indicators"
                return self._finalize_result(result, start_time)

            # === GENERATE SIGNAL (via strategy) ===
            signal = self.strategy.generate_signal(
                df_with_ind, symbol, indicators.close
            )

            if signal is None:
                result.rejection_reason = "No signal conditions met"
                result.rejection_stage = "direction"
                return self._finalize_result(result, start_time)

            # === SCORE CHECK ===
            if signal.score < self.min_score:
                result.rejection_reason = (
                    f"Score too low: {signal.score:.1f} < {self.min_score}"
                )
                result.rejection_stage = "score"
                result.raw_score = signal.score
                return self._finalize_result(result, start_time)

            result.raw_score = signal.score

            # === GET TICKER (for spread check) ===
            ticker = self.price_feed.get_ticker(symbol)

            # === FILTER CHECK ===
            filter_result = self.filter.check(
                symbol=symbol,
                direction=signal.direction,
                score=signal.score,
                indicators=indicators,
                breakdown=signal.score_breakdown,
                current_positions=current_positions,
                spread_pct=ticker.spread_pct if ticker else None,
            )

            result.filter_result = filter_result

            if not filter_result.passed:
                failed = [f.name for f in filter_result.failed_filters]
                result.rejection_reason = f"Filter failed: {', '.join(failed)}"
                result.rejection_stage = "filter"
                return self._finalize_result(result, start_time)

            # === SUCCESS ===
            # Enrich signal
            signal.indicators = indicators
            signal.filter_result = filter_result
            signal.strategy_name = self.strategy.name
            signal.regime = self.current_regime
            signal.confidence = self.strategy.get_confidence(signal.score)
            signal.timeframe = timeframe  # Add timeframe to signal

            # Add to history
            self.history.add(signal, timeframe)

            # Fire callbacks
            self._fire_callbacks(signal)

            result.signal = signal
            result.success = True

            log.signal(
                f"Signal [{timeframe}]: {symbol} {signal.direction} @ {signal.price:.8f} "
                f"(score: {signal.score:.1f}, tier: {signal.quality_tier})"
            )

            return self._finalize_result(result, start_time)

        except Exception as e:
            result.rejection_reason = f"Error: {str(e)}"
            result.rejection_stage = "error"
            log.error(f"Signal generation error for {symbol} [{timeframe}]: {e}")
            return self._finalize_result(result, start_time)

    def _finalize_result(
        self, result: GenerationResult, start_time: datetime
    ) -> GenerationResult:
        """Add timing to result."""
        result.generation_time_ms = (datetime.now() - start_time).total_seconds() * 1000
        return result

    # =========================================================================
    # MULTI-TIMEFRAME (MTF) GENERATION
    # =========================================================================

    def generate_mtf(
        self,
        symbol: str,
        timeframes: Optional[List[str]] = None,
        check_cooldown: bool = True,
        current_positions: int = 0,
    ) -> MTFSignalResult:
        """
        Generate signal with multi-timeframe analysis.

        Analyzes signal across multiple timeframes and determines
        consensus direction with alignment score.

        Args:
            symbol: Trading symbol
            timeframes: List of timeframes (None = use configured MTF)
            check_cooldown: Check cooldown
            current_positions: Open positions

        Returns:
            MTFSignalResult with consensus and per-timeframe breakdown
        """
        timeframes = timeframes or self.mtf_timeframes

        # Validate timeframes
        valid_tfs = [tf for tf in timeframes if validate_timeframe(tf)]

        if not valid_tfs:
            log.error(f"No valid timeframes for MTF: {timeframes}")
            return MTFSignalResult(symbol=symbol, timestamp=datetime.now())

        # Sort by duration (fastest first)
        valid_tfs = sorted(valid_tfs, key=get_timeframe_minutes)

        result = MTFSignalResult(symbol=symbol, timestamp=datetime.now())

        # Generate signals for each timeframe
        for tf in valid_tfs:
            gen_result = self.generate_with_details(
                symbol=symbol,
                timeframe=tf,
                check_cooldown=check_cooldown,
                current_positions=current_positions,
            )

            result.timeframe_signals[tf] = gen_result.signal

            if gen_result.signal:
                direction = gen_result.signal.direction
                result.timeframe_directions[tf] = direction

                if direction == "LONG":
                    result.bullish_timeframes += 1
                elif direction == "SHORT":
                    result.bearish_timeframes += 1
                else:
                    result.neutral_timeframes += 1
            else:
                result.timeframe_directions[tf] = None
                result.neutral_timeframes += 1

        # Calculate consensus
        total_tfs = len(valid_tfs)

        if result.bullish_timeframes > result.bearish_timeframes:
            result.consensus_direction = "LONG"
            result.alignment_score = (result.bullish_timeframes / total_tfs) * 100
        elif result.bearish_timeframes > result.bullish_timeframes:
            result.consensus_direction = "SHORT"
            result.alignment_score = (result.bearish_timeframes / total_tfs) * 100
        else:
            result.consensus_direction = None
            result.alignment_score = 0

        # Check alignment
        alignment_ratio = (
            max(result.bullish_timeframes, result.bearish_timeframes) / total_tfs
        )
        result.is_aligned = alignment_ratio >= self.mtf_alignment_threshold

        # Check for conflicting signals (both LONG and SHORT present)
        result.conflicting = (
            result.bullish_timeframes > 0 and result.bearish_timeframes > 0
        )

        # Select primary signal (from fastest aligned timeframe)
        if result.is_aligned and result.consensus_direction:
            for tf in valid_tfs:
                signal = result.timeframe_signals.get(tf)
                if signal and signal.direction == result.consensus_direction:
                    result.primary_signal = signal
                    break

        log.info(
            f"MTF [{symbol}]: {result.bullish_timeframes}↑ / {result.bearish_timeframes}↓ / "
            f"{result.neutral_timeframes}─ = {result.consensus_direction or 'NONE'} "
            f"({result.alignment_score:.0f}% aligned)"
        )

        return result

    def generate_with_mtf_confirmation(
        self,
        symbol: str,
        primary_timeframe: Optional[str] = None,
        confirmation_timeframes: Optional[List[str]] = None,
        require_alignment: bool = True,
    ) -> Optional[Signal]:
        """
        Generate signal with MTF confirmation.

        Signal is only returned if higher timeframes align.

        Args:
            symbol: Trading symbol
            primary_timeframe: Main signal timeframe
            confirmation_timeframes: Higher timeframes to confirm
            require_alignment: Require all confirmation TFs to align

        Returns:
            Signal if confirmed, None otherwise
        """
        primary_tf = primary_timeframe or self.default_timeframe

        # Default confirmation timeframes (higher than primary)
        if confirmation_timeframes is None:
            primary_minutes = get_timeframe_minutes(primary_tf)
            confirmation_timeframes = [
                tf
                for tf in self.mtf_timeframes
                if get_timeframe_minutes(tf) > primary_minutes
            ]

        if not confirmation_timeframes:
            # No confirmation TFs, just return primary signal
            return self.generate(symbol, timeframe=primary_tf)

        # Generate primary signal
        primary_signal = self.generate(symbol, timeframe=primary_tf)

        if not primary_signal:
            return None

        # Check confirmation
        confirmations = 0
        total_confirms = len(confirmation_timeframes)

        for conf_tf in confirmation_timeframes:
            conf_result = self.generate_with_details(
                symbol,
                timeframe=conf_tf,
                check_cooldown=False,  # Don't check cooldown for confirmation
            )

            if conf_result.signal:
                if conf_result.signal.direction == primary_signal.direction:
                    confirmations += 1
                    log.debug(
                        f"MTF Confirm: {conf_tf} ✓ {conf_result.signal.direction}"
                    )
                else:
                    log.debug(
                        f"MTF Conflict: {conf_tf} ✗ {conf_result.signal.direction}"
                    )
            else:
                log.debug(f"MTF No signal: {conf_tf}")

        # Check if confirmed
        if require_alignment:
            # All must confirm
            if confirmations == total_confirms:
                log.info(
                    f"MTF Confirmed [{symbol}]: {confirmations}/{total_confirms} aligned"
                )
                return primary_signal
            else:
                log.info(
                    f"MTF Rejected [{symbol}]: {confirmations}/{total_confirms} aligned"
                )
                return None
        else:
            # Majority confirms
            if confirmations >= total_confirms / 2:
                log.info(
                    f"MTF Confirmed [{symbol}]: {confirmations}/{total_confirms} aligned"
                )
                return primary_signal
            else:
                log.info(
                    f"MTF Rejected [{symbol}]: {confirmations}/{total_confirms} aligned"
                )
                return None

    # =========================================================================
    # BATCH GENERATION
    # =========================================================================

    def generate_batch(
        self,
        symbols: List[str],
        timeframe: Optional[str] = None,
        check_cooldown: bool = True,
        current_positions: int = 0,
        parallel: bool = False,
    ) -> BatchGenerationResult:
        """
        Generate signals for multiple symbols.

        NOW SUPPORTS MTF: If mtf_enabled=True, uses MTF confirmation logic

        Args:
            symbols: List of symbols
            timeframe: Timeframe (None = default)
            check_cooldown: Check cooldown
            current_positions: Open positions
            parallel: Use parallel processing (future)

        Returns:
            BatchGenerationResult
        """
        start_time = datetime.now()
        timeframe = timeframe or self.default_timeframe

        results = []
        signals = []
        rejection_reasons: Dict[str, int] = {}
        errors = 0

        for symbol in symbols:
            if self.mtf_enabled:
                try:
                    # Use MTF confirmation with higher timeframes
                    signal = self.generate_with_mtf_confirmation(
                        symbol=symbol,
                        primary_timeframe=timeframe,
                        check_cooldown=check_cooldown,
                    )

                    # Create GenerationResult to match expected format
                    result = GenerationResult(
                        symbol=symbol, timestamp=start_time, timeframe=timeframe
                    )

                    if signal:
                        result.success = True
                        result.signal = signal
                        signals.append(signal)
                    else:
                        result.success = False
                        result.rejection_reason = "MTF alignment failed"
                        result.rejection_stage = "mtf_filter"
                        rejection_reasons["mtf_filter"] = (
                            rejection_reasons.get("mtf_filter", 0) + 1
                        )

                    results.append(result)

                except Exception as e:
                    errors += 1
                    log.error(f"MTF generation error for {symbol}: {e}")

            else:
                # Standard single-timeframe logic
                result = self.generate_with_details(
                    symbol=symbol,
                    timeframe=timeframe,
                    check_cooldown=check_cooldown,
                    current_positions=current_positions,
                )

                results.append(result)

                if result.success and result.signal:
                    signals.append(result.signal)
                elif result.rejection_stage == "error":
                    errors += 1
                else:
                    # Track rejection reasons
                    reason = result.rejection_stage or "unknown"
                    rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1

        total_time = (datetime.now() - start_time).total_seconds() * 1000

        batch_result = BatchGenerationResult(
            timestamp=start_time,
            timeframe=timeframe,
            results=results,
            signals=signals,
            total_symbols=len(symbols),
            successful=len(signals),
            rejected=len(symbols) - len(signals) - errors,
            errors=errors,
            total_time_ms=total_time,
            rejection_reasons=rejection_reasons,
        )

        mode_str = "MTF" if self.mtf_enabled else timeframe
        log.info(
            f"Batch [{mode_str}]: {len(signals)}/{len(symbols)} signals "
            f"({batch_result.success_rate:.1f}% success, {total_time:.0f}ms)"
        )

        return batch_result

    def generate_ranked(
        self,
        symbols: List[str],
        top_n: int = 5,
        timeframe: Optional[str] = None,
        check_cooldown: bool = True,
        current_positions: int = 0,
    ) -> List[Signal]:
        """
        Generate and rank signals by score.

        Args:
            symbols: List of symbols
            top_n: Number of top signals to return
            timeframe: Timeframe
            check_cooldown: Check cooldown
            current_positions: Open positions

        Returns:
            Top N signals sorted by score (highest first)
        """
        batch_result = self.generate_batch(
            symbols=symbols,
            timeframe=timeframe,
            check_cooldown=check_cooldown,
            current_positions=current_positions,
        )

        return batch_result.get_top_signals(top_n)

    def generate_best(
        self,
        symbols: List[str],
        timeframe: Optional[str] = None,
        check_cooldown: bool = True,
        current_positions: int = 0,
    ) -> Optional[Signal]:
        """
        Generate and return single best signal.

        Args:
            symbols: List of symbols
            timeframe: Timeframe
            check_cooldown: Check cooldown
            current_positions: Open positions

        Returns:
            Best signal or None
        """
        top = self.generate_ranked(
            symbols=symbols,
            top_n=1,
            timeframe=timeframe,
            check_cooldown=check_cooldown,
            current_positions=current_positions,
        )

        return top[0] if top else None

    # =========================================================================
    # REGIME HANDLING
    # =========================================================================

    def set_regime(self, regime: str):
        """
        Set current market regime.

        Args:
            regime: 'bull', 'bear', 'range', 'crash'
        """
        old_regime = self.current_regime
        self.current_regime = regime

        # Adjust min_score based on regime
        if regime == "crash":
            self.min_score = self.config.get(
                "regime_overrides", "crash", "min_signal_score", default=85
            )
        elif regime == "bear":
            self.min_score = self.config.get(
                "regime_overrides", "bear", "min_signal_score", default=75
            )
        elif regime == "bull":
            self.min_score = self.config.get(
                "regime_overrides", "bull", "min_signal_score", default=55
            )
        else:
            self.min_score = self.config.get("scanner", "min_signal_score", default=70)

        if old_regime != regime:
            log.info(
                f"Regime changed: {old_regime} → {regime} (min_score: {self.min_score})"
            )

    # =========================================================================
    # CALLBACKS
    # =========================================================================

    def on_signal(self, callback: Callable[[Signal], None]):
        """
        Register callback for new signals.

        Args:
            callback: Function to call with Signal
        """
        self._on_signal_callbacks.append(callback)

    def _fire_callbacks(self, signal: Signal):
        """Fire all registered callbacks."""
        for callback in self._on_signal_callbacks:
            try:
                callback(signal)
            except Exception as e:
                log.error(f"Callback error: {e}")

    # =========================================================================
    # CONFIGURATION
    # =========================================================================

    def set_min_score(self, score: float):
        """Set minimum signal score."""
        self.min_score = score
        log.info(f"Min score set to: {score}")

    def set_cooldown(self, minutes: int):
        """Set cooldown period."""
        self.cooldown_minutes = minutes
        log.info(f"Cooldown set to: {minutes} minutes")

    # =========================================================================
    # UTILITY
    # =========================================================================

    def get_stats(self) -> dict:
        """Get generator statistics."""
        history_stats = self.history.get_stats()

        return {
            "strategy": self.strategy.name,
            "default_timeframe": self.default_timeframe,
            "mtf_enabled": self.mtf_enabled,
            "mtf_timeframes": self.mtf_timeframes,
            "min_score": self.min_score,
            "cooldown_minutes": self.cooldown_minutes,
            "current_regime": self.current_regime,
            "history": history_stats,
        }

    def reset_cooldowns(self):
        """Reset all cooldowns."""
        self.filter.clear_all_cooldowns()
        log.info("Cooldowns reset")

    def clear_history(self):
        """Clear signal history."""
        self.history.clear()
        log.info("History cleared")

    @staticmethod
    def get_valid_timeframes() -> List[str]:
        """Get list of valid timeframes."""
        return VALID_TIMEFRAMES.copy()


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================


def create_generator(
    exchange: ExchangeBase, config: Config, strategy: Strategy
) -> SignalGenerator:
    """Create signal generator instance."""
    return SignalGenerator(exchange, config, strategy)
