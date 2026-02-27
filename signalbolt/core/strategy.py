"""
Base strategy class and strategy interface.

All trading strategies inherit from Strategy and implement:
- generate_signal()
- calculate_entry()
- calculate_exits()

This ensures consistent interface across all strategy variants.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime
import pandas as pd

from signalbolt.core.config import Config
from signalbolt.core.indicators import IndicatorValues, IndicatorCalculator
from signalbolt.core.scoring import ScoreBreakdown, calculate_score
from signalbolt.core.filters import FilterChainResult, SignalFilter
from signalbolt.exchange.base import Ticker
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.core.strategy")


# =============================================================================
# SIGNAL DATA STRUCTURES
# =============================================================================


@dataclass
class Signal:
    """Trading signal with full context."""

    # =========================================================================
    # REQUIRED FIELDS (no default) - MUST BE FIRST
    # =========================================================================
    symbol: str
    direction: str  # 'LONG' or 'SHORT'
    timestamp: datetime
    score: float
    price: float

    # =========================================================================
    # OPTIONAL FIELDS (with default) - MUST BE LAST
    # =========================================================================
    score_breakdown: Optional[ScoreBreakdown] = None
    indicators: Optional[IndicatorValues] = None
    filter_result: Optional[FilterChainResult] = None
    strategy_name: str = ""
    regime: str = "unknown"
    confidence: str = "medium"  # 'low', 'medium', 'high'
    timeframe: str = "5m"  # Added timeframe field
    notes: str = ""

    def to_dict(self) -> dict:
        """Convert to dict for logging/storage."""
        result = {
            "symbol": self.symbol,
            "direction": self.direction,
            "timestamp": self.timestamp.isoformat(),
            "score": round(self.score, 1),
            "price": self.price,
            "strategy": self.strategy_name,
            "regime": self.regime,
            "confidence": self.confidence,
            "timeframe": self.timeframe,
        }

        if self.indicators:
            result["indicators"] = self.indicators.to_dict()

        if self.score_breakdown:
            result["score_breakdown"] = self.score_breakdown.to_dict()

        if self.filter_result:
            result["passed_filters"] = self.filter_result.passed
            result["has_warnings"] = self.filter_result.has_warnings
            if self.filter_result.warnings:
                result["warnings"] = [w.name for w in self.filter_result.warnings]

        if self.notes:
            result["notes"] = self.notes

        return result

    @property
    def quality_tier(self) -> str:
        """Get signal quality tier based on score."""
        if self.score >= 85:
            return "PREMIUM"
        elif self.score >= 75:
            return "HIGH"
        elif self.score >= 65:
            return "GOOD"
        elif self.score >= 55:
            return "FAIR"
        else:
            return "WEAK"


@dataclass
class EntryPlan:
    """Entry execution plan."""

    # Required fields
    symbol: str
    direction: str
    entry_price: float
    position_size_usd: float
    quantity: float
    stop_loss_price: float
    stop_loss_pct: float

    # Optional fields
    use_limit: bool = False
    limit_price: Optional[float] = None
    limit_offset_pct: float = 0.05
    limit_timeout_sec: int = 30
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "size_usd": self.position_size_usd,
            "quantity": self.quantity,
            "stop_loss": self.stop_loss_price,
            "stop_loss_pct": self.stop_loss_pct,
            "use_limit": self.use_limit,
            "limit_price": self.limit_price,
        }


@dataclass
class ExitPlan:
    """Exit levels and conditions."""

    # Required fields
    stop_loss_price: float
    stop_loss_pct: float

    # Optional fields
    take_profit_price: Optional[float] = None
    take_profit_pct: Optional[float] = None

    # Trailing stop
    trailing_active: bool = False
    trailing_activation_pct: float = 0.5
    trailing_distance_pct: float = 0.4

    # Break-even
    breakeven_enabled: bool = True
    breakeven_activation_pct: float = 0.5
    breakeven_offset_pct: float = 0.05

    # Timeout
    timeout_minutes: int = 60
    min_profit_on_timeout_pct: float = 0.15

    def to_dict(self) -> dict:
        return {
            "stop_loss": self.stop_loss_price,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit": self.take_profit_price,
            "take_profit_pct": self.take_profit_pct,
            "trailing_active": self.trailing_active,
            "trailing_activation": self.trailing_activation_pct,
            "trailing_distance": self.trailing_distance_pct,
            "breakeven_enabled": self.breakeven_enabled,
            "breakeven_activation": self.breakeven_activation_pct,
            "timeout_min": self.timeout_minutes,
        }


# =============================================================================
# BASE STRATEGY
# =============================================================================


class Strategy(ABC):
    """
    Base class for all trading strategies.

    Usage:
        class MyStrategy(Strategy):
            def generate_signal(self, df, symbol):
                # Your logic
                return Signal(...)

        strategy = MyStrategy(config)
        signal = strategy.analyze(df, 'BTCUSDT')
    """

    def __init__(self, config: Config):
        """
        Initialize strategy.

        Args:
            config: Configuration instance
        """
        self.config = config
        self._name = self.__class__.__name__

        # Components
        self.indicator_calculator = IndicatorCalculator(
            enable_macd=True, enable_bb=True, enable_stoch=True
        )

        self.filter = SignalFilter(config)

        # State
        self._last_signal_time: Dict[str, datetime] = {}

        log.info(f"Strategy initialized: {self.name}")

    @property
    def name(self) -> str:
        """Strategy name (derived from class name)."""
        return self._name

    # =========================================================================
    # ABSTRACT METHODS (must implement)
    # =========================================================================

    @abstractmethod
    def generate_signal(
        self, df: pd.DataFrame, symbol: str, current_price: Optional[float] = None
    ) -> Optional[Signal]:
        """
        Generate trading signal from OHLCV data.

        Args:
            df: OHLCV DataFrame (columns: open, high, low, close, volume)
            symbol: Trading symbol
            current_price: Current market price (optional, uses last close if None)

        Returns:
            Signal instance if conditions met, None otherwise

        Note: This method should NOT modify df (make a copy if needed)
        """
        pass

    @abstractmethod
    def calculate_entry(
        self, signal: Signal, ticker: Ticker, wallet_balance: float
    ) -> EntryPlan:
        """
        Calculate entry execution plan.

        Args:
            signal: Generated signal
            ticker: Current market ticker
            wallet_balance: Available balance

        Returns:
            EntryPlan with sizing and prices
        """
        pass

    @abstractmethod
    def calculate_exits(
        self, entry_price: float, direction: str, indicators: IndicatorValues
    ) -> ExitPlan:
        """
        Calculate exit levels (SL, TP, trailing).

        Args:
            entry_price: Entry price
            direction: 'LONG' or 'SHORT'
            indicators: Current indicator values

        Returns:
            ExitPlan with all exit levels
        """
        pass

    # =========================================================================
    # PUBLIC INTERFACE
    # =========================================================================

    def analyze(
        self,
        df: pd.DataFrame,
        symbol: str,
        ticker: Optional[Ticker] = None,
        current_positions: int = 0,
        wallet_balance: float = 0.0,
    ) -> Optional[Signal]:
        """
        Full analysis pipeline: indicators -> signal -> filters.

        Args:
            df: OHLCV data
            symbol: Trading symbol
            ticker: Current ticker (for spread check)
            current_positions: Number of open positions
            wallet_balance: Wallet balance

        Returns:
            Signal if valid, None otherwise
        """
        # 1. Calculate indicators
        try:
            df_with_indicators = self.indicator_calculator.calculate(df, symbol)
            indicators = self.indicator_calculator.get_latest(df_with_indicators)
        except Exception as e:
            log.error(f"Indicator calculation failed for {symbol}: {e}")
            return None

        # 2. Generate signal
        signal = self.generate_signal(df_with_indicators, symbol)

        if signal is None:
            return None

        # 3. Calculate score if not already done
        if signal.score_breakdown is None and signal.indicators:
            signal.score_breakdown = calculate_score(
                signal.indicators, signal.direction, enable_bonus=True
            )
            signal.score = signal.score_breakdown.total

        # 4. Filter validation
        spread_pct = ticker.spread_pct if ticker else None

        filter_result = self.filter.check(
            symbol=symbol,
            direction=signal.direction,
            score=signal.score,
            indicators=indicators,
            breakdown=signal.score_breakdown,
            current_positions=current_positions,
            spread_pct=spread_pct,
        )

        signal.filter_result = filter_result

        # 5. Check if passed (hard reject only)
        if not filter_result.passed:
            log.debug(f"Signal rejected: {symbol} - {filter_result.summary()}")
            return None

        # 6. Log warnings if any
        if filter_result.has_warnings:
            log.warning(f"Signal has warnings: {symbol}")
            for warning in filter_result.warnings:
                log.warning(f"  - {warning.name}: {warning.reason}")

        # Update last signal time
        self._last_signal_time[symbol] = datetime.now()

        log.signal(
            f"Signal generated: {symbol} {signal.direction} @ {signal.price:.8f} "
            f"(score: {signal.score:.1f}, tier: {signal.quality_tier})"
        )

        return signal

    def can_generate_signal(self, symbol: str) -> bool:
        """Check if enough time passed since last signal (cooldown)."""
        if symbol not in self._last_signal_time:
            return True

        cooldown_min = self.config.get("scanner", "signal_cooldown_min", default=30)
        last_signal = self._last_signal_time[symbol]

        elapsed_min = (datetime.now() - last_signal).total_seconds() / 60

        return elapsed_min >= cooldown_min

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    def get_min_data_length(self) -> int:
        """Minimum candles needed for analysis."""
        return 100  # Safe default (EMA50 + some buffer)

    def get_confidence(self, score: float) -> str:
        """Get confidence level from score."""
        if score >= 80:
            return "high"
        elif score >= 65:
            return "medium"
        else:
            return "low"

    def __repr__(self) -> str:
        return f"{self.name}(min_score={self.config.get('scanner', 'min_signal_score', default=70)})"


# =============================================================================
# STRATEGY FACTORY
# =============================================================================


def create_strategy(strategy_name: str, config: Config) -> Strategy:
    """
    Create strategy instance by name.

    Args:
        strategy_name: Strategy class name (e.g., 'SignalBoltOriginal')
        config: Config instance

    Returns:
        Strategy instance

    Usage:
        strategy = create_strategy('SignalBoltOriginal', config)
    """
    import importlib

    MODULE_MAP = {
        "SignalBoltOriginal": "signalbolt.strategies.SignalBolt_original",
        "SignalBolt_original": "signalbolt.strategies.SignalBolt_original",
        "SignalBoltAggressive": "signalbolt.strategies.SignalBolt_original",
        "SignalBoltConservative": "signalbolt.strategies.SignalBolt_original",
        "SignalBoltScalper": "signalbolt.strategies.SignalBolt_original",
    }

    CLASS_MAP = {
        "SignalBolt_original": "SignalBoltOriginal",
        "signalbolt_original": "SignalBoltOriginal",
    }

    # Get module path
    module_path = MODULE_MAP.get(
        strategy_name, f"signalbolt.strategies.{strategy_name}"
    )

    # Get class name
    class_name = CLASS_MAP.get(strategy_name, strategy_name)

    try:
        # Import module
        module = importlib.import_module(module_path)

        # Get class
        strategy_class = getattr(module, class_name)

        # Create instance
        strategy = strategy_class(config)

        log.info(f"Loaded strategy: {class_name}")

        return strategy

    except ImportError as e:
        log.error(f"Failed to import strategy module {module_path}: {e}")
        raise
    except AttributeError as e:
        log.error(f"Strategy class '{class_name}' not found in {module_path}: {e}")
        log.error(
            f"Available classes: {[name for name in dir(module) if not name.startswith('_')]}"
        )
        raise
    except Exception as e:
        log.error(f"Failed to create strategy {strategy_name}: {e}")
        raise


# =============================================================================
# STRATEGY FACTORY (Dynamic Import - No Circular Deps)
# =============================================================================


def create_strategy(name: str, config: "Config") -> "Strategy":
    """
    Create strategy instance by name using dynamic import.

    This avoids circular import issues by importing strategy modules
    only when needed, not at module load time.

    Args:
        name: Strategy name (full or alias)
        config: Config instance

    Returns:
        Strategy instance

    Raises:
        ValueError: If strategy not found
        ImportError: If strategy module cannot be loaded

    Example:
        >>> from signalbolt.core.strategy import create_strategy
        >>> from signalbolt.core.config import Config
        >>>
        >>> config = Config("config.yaml")
        >>> strategy = create_strategy("SignalBoltScalper", config)
    """
    import importlib

    # Strategy aliases
    ALIASES = {
        "original": "SignalBoltOriginal",
        "scalper": "SignalBoltScalper",
        "conservative": "SignalBoltConservative",
        "aggressive": "SignalBoltAggressive",
        "adaptive": "SignalBoltAdaptive",
        "default": "SignalBoltOriginal",
        "safe": "SignalBoltConservative",
        "fast": "SignalBoltScalper",
        "risky": "SignalBoltAggressive",
        "auto": "SignalBoltAdaptive",
    }

    # Resolve alias
    resolved_name = ALIASES.get(name.lower(), name)

    # Strategy mapping: Name -> (module_path, class_name)
    STRATEGY_MAP = {
        "SignalBoltOriginal": (
            "signalbolt.strategies.SignalBolt_original",
            "SignalBoltOriginal",
        ),
        "SignalBoltScalper": (
            "signalbolt.strategies.SignalBolt_scalper",
            "SignalBoltScalper",
        ),
        "SignalBoltConservative": (
            "signalbolt.strategies.SignalBolt_conservative",
            "SignalBoltConservative",
        ),
        "SignalBoltAggressive": (
            "signalbolt.strategies.SignalBolt_aggressive",
            "SignalBoltAggressive",
        ),
        "SignalBoltAdaptive": (
            "signalbolt.strategies.SignalBolt_adaptive",
            "SignalBoltAdaptive",
        ),
    }

    if resolved_name not in STRATEGY_MAP:
        available = list(STRATEGY_MAP.keys()) + list(ALIASES.keys())
        raise ValueError(
            f"Unknown strategy: '{name}'. Available: {sorted(set(available))}"
        )

    module_path, class_name = STRATEGY_MAP[resolved_name]

    try:
        # Dynamic import - only loads when called
        module = importlib.import_module(module_path)
        strategy_class = getattr(module, class_name)
        return strategy_class(config)
    except ImportError as e:
        raise ImportError(
            f"Failed to import strategy '{resolved_name}' from {module_path}: {e}"
        ) from e
    except AttributeError as e:
        raise ImportError(
            f"Strategy class '{class_name}' not found in {module_path}: {e}"
        ) from e


def list_strategies() -> list[str]:
    """
    Get list of available strategy names.

    Returns:
        List of strategy names (full names only)

    Example:
        >>> from signalbolt.core.strategy import list_strategies
        >>> print(list_strategies())
        ['SignalBoltOriginal', 'SignalBoltScalper', ...]
    """
    return [
        "SignalBoltOriginal",
        "SignalBoltScalper",
        "SignalBoltConservative",
        "SignalBoltAggressive",
        "SignalBoltAdaptive",
    ]


def get_strategy_aliases() -> dict[str, str]:
    """
    Get mapping of aliases to strategy names.

    Returns:
        Dict mapping alias -> strategy name
    """
    return {
        "original": "SignalBoltOriginal",
        "scalper": "SignalBoltScalper",
        "conservative": "SignalBoltConservative",
        "aggressive": "SignalBoltAggressive",
        "adaptive": "SignalBoltAdaptive",
        "default": "SignalBoltOriginal",
        "safe": "SignalBoltConservative",
        "fast": "SignalBoltScalper",
        "risky": "SignalBoltAggressive",
        "auto": "SignalBoltAdaptive",
    }
