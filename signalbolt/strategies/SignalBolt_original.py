"""
SignalBolt Original Strategy

Classic EMA alignment + ADX + RSI strategy with regime-aware parameters.

Entry Logic:
- LONG: EMA9 > EMA21 > EMA50
- ADX > threshold (trending market)
- RSI in healthy range
- Volume above average
- Score >= min_score (regime-adjusted)

Exit Logic:
- Stop-loss (regime-based %)
- Trailing stop (activated at breakeven trigger)
- Timeout (60 min default)

Multi-Timeframe Support:
- Analyzes signal on configured timeframe(s)
- Can require higher TF confirmation
- Regime detection uses higher timeframe data
"""

from typing import Optional
from datetime import datetime
import pandas as pd

from signalbolt.core.strategy import Strategy, Signal, EntryPlan, ExitPlan
from signalbolt.core.config import Config
from signalbolt.core.indicators import IndicatorValues
from signalbolt.core.scoring import calculate_score
from signalbolt.core.risk import RiskManager
from signalbolt.exchange.base import Ticker
from signalbolt.regime.detector import RegimeDetector, MarketRegime
from signalbolt.regime.presets import get_regime_preset, RegimePreset
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.strategies.original")


class SignalBoltOriginal(Strategy):
    """
    Original SignalBolt strategy.

    Features:
    - EMA trend alignment
    - ADX trend strength filter
    - RSI momentum filter
    - Volume confirmation
    - Regime-aware parameters
    - Dynamic score thresholds
    - Multi-timeframe support
    """

    def __init__(self, config: Config):
        """Initialize strategy."""
        super().__init__(config)

        self.regime_detector = RegimeDetector()
        self.risk_manager = RiskManager(config)

        # Strategy parameters (from config)
        self.min_adx = config.get("strategy", "min_adx", default=25.0)
        self.strong_adx = config.get("strategy", "strong_adx", default=40.0)

        self.rsi_min = config.get("strategy", "rsi_min", default=40.0)
        self.rsi_max = config.get("strategy", "rsi_max", default=70.0)
        self.rsi_oversold = config.get("strategy", "rsi_oversold", default=30.0)
        self.rsi_overbought = config.get("strategy", "rsi_overbought", default=70.0)

        self.volume_multiplier = config.get(
            "strategy", "volume_multiplier", default=1.2
        )
        self.volume_spike = config.get("strategy", "volume_spike", default=2.0)

        # Timeframe settings
        self.timeframe = config.get("strategy", "timeframe", default="5m")
        self.mtf_enabled = config.get("strategy", "mtf_enabled", default=False)
        self.mtf_timeframes = config.get(
            "strategy", "mtf_timeframes", default=["5m", "15m", "1h"]
        )

        # ATR settings
        self.atr_period = config.get("strategy", "atr_period", default=14)
        self.use_atr_sl = config.get("spot", "use_atr_sl", default=False)
        self.atr_sl_multiplier = config.get(
            "strategy", "atr_multiplier_sl", default=1.5
        )
        self.atr_tp_multiplier = config.get(
            "strategy", "atr_multiplier_tp", default=2.5
        )

        log.info(
            f"SignalBolt Original initialized "
            f"(timeframe={self.timeframe}, min_adx={self.min_adx}, "
            f"rsi={self.rsi_min}-{self.rsi_max}, mtf={self.mtf_enabled})"
        )

    # =========================================================================
    # ABSTRACT METHODS IMPLEMENTATION
    # =========================================================================

    def generate_signal(
        self, df: pd.DataFrame, symbol: str, current_price: Optional[float] = None
    ) -> Optional[Signal]:
        """
        Generate signal from OHLCV data.

        Logic:
        1. Get latest indicators
        2. Check EMA alignment
        3. Check ADX (trend strength)
        4. Check RSI (not overbought)
        5. Check volume
        6. Calculate score
        7. Check min_score (regime-adjusted)
        8. Create signal

        Args:
            df: OHLCV dataframe with indicators
            symbol: Trading symbol
            current_price: Current price (optional, uses last close)

        Returns:
            Signal if conditions met, None otherwise
        """

        # 1. Get indicators (already calculated by analyze())
        indicators = self.indicator_calculator.get_latest(df)

        if current_price is None:
            current_price = df["close"].iloc[-1]

        # 2. Detect regime
        regime = self.regime_detector.detect(df)

        # 3. Check EMA alignment
        direction = self._check_ema_alignment(indicators)

        if direction is None:
            log.debug(f"{symbol}: No EMA alignment")
            return None

        # 4. Check ADX (trend strength)
        if indicators.adx < self.min_adx:
            log.debug(f"{symbol}: ADX too low ({indicators.adx:.1f} < {self.min_adx})")
            return None

        # 5. Check RSI
        if not self._check_rsi(indicators, direction):
            log.debug(f"{symbol}: RSI check failed ({indicators.rsi:.1f})")
            return None

        # 6. Check volume
        if not self._check_volume(df):
            log.debug(f"{symbol}: Volume too low")
            return None

        # 7. Check DI alignment (for direction confirmation)
        if not self._check_di_alignment(indicators, direction):
            log.debug(f"{symbol}: DI alignment failed")
            return None

        # 8. Calculate score
        score_breakdown = calculate_score(indicators, direction, enable_bonus=True)

        # 9. Get min_score (regime-adjusted)
        preset = get_regime_preset(regime, self.config)
        min_score = preset.min_signal_score

        if score_breakdown.total < min_score:
            log.debug(
                f"{symbol}: Score too low ({score_breakdown.total:.1f} < {min_score})"
            )
            return None

        # 10. Build signal notes
        notes = self._build_signal_notes(indicators, regime, df)

        # 11. Create signal
        signal = Signal(
            symbol=symbol,
            direction=direction,
            timestamp=datetime.now(),
            score=score_breakdown.total,
            score_breakdown=score_breakdown,
            price=current_price,
            indicators=indicators,
            strategy_name=self.name,
            regime=regime.value,
            confidence=self.get_confidence(score_breakdown.total),
            timeframe=self.timeframe,
            notes=notes,
        )

        return signal

    def calculate_entry(
        self, signal: Signal, ticker: Ticker, wallet_balance: float
    ) -> EntryPlan:
        """
        Calculate entry plan.

        Logic:
        1. Get entry price (market or limit)
        2. Get regime preset for parameters
        3. Calculate stop-loss (fixed % or ATR-based)
        4. Calculate position size (via RiskManager)
        5. Calculate quantity
        6. Create entry plan

        Args:
            signal: Trading signal
            ticker: Current ticker data
            wallet_balance: Available balance

        Returns:
            EntryPlan with all entry details
        """

        # 1. Entry price
        use_limit = (
            self.config.get("spot", "entry_order_type", default="MARKET") == "LIMIT"
        )

        if use_limit:
            # Place limit order at bid/ask
            if signal.direction == "LONG":
                entry_price = ticker.bid
            else:  # SHORT
                entry_price = ticker.ask
        else:
            # Market order
            entry_price = ticker.last_price

        # 2. Get regime preset
        regime = MarketRegime(signal.regime)
        preset = get_regime_preset(regime, self.config)

        # 3. Calculate stop-loss price
        sl_price = self._calculate_stop_loss(
            entry_price=entry_price,
            direction=signal.direction,
            indicators=signal.indicators,
            preset=preset,
        )

        # 4. Calculate position size
        position_size = self.risk_manager.calculate_position_size(
            balance=wallet_balance,
            entry_price=entry_price,
            stop_loss_price=sl_price,
            direction=signal.direction,
        )

        # 5. Calculate quantity
        quantity = position_size.size_usd / entry_price

        # 6. Calculate stop-loss %
        if signal.direction == "LONG":
            sl_pct = ((entry_price - sl_price) / entry_price) * 100
        else:
            sl_pct = ((sl_price - entry_price) / entry_price) * 100

        # 7. Build notes
        notes = f"Risk: ${position_size.risk_usd:.2f} ({position_size.risk_pct:.2f}%), "
        notes += f"SL: {sl_pct:.2f}%, "
        notes += f"Regime: {regime.value}"

        if self.use_atr_sl and signal.indicators:
            notes += f", ATR-based SL ({self.atr_sl_multiplier}x)"

        # 8. Create entry plan
        plan = EntryPlan(
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=entry_price,
            position_size_usd=position_size.size_usd,
            quantity=quantity,
            stop_loss_price=sl_price,
            stop_loss_pct=sl_pct,
            use_limit=use_limit,
            limit_offset_pct=self.config.get("spot", "limit_offset_pct", default=0.05),
            notes=notes,
        )

        return plan

    def calculate_exits(
        self, entry_price: float, direction: str, indicators: IndicatorValues
    ) -> ExitPlan:
        """
        Calculate exit levels.

        Uses regime-aware parameters for:
        - Stop-loss % (or ATR-based)
        - Trailing activation %
        - Trailing distance %
        - Timeout minutes
        - Take profit (optional)

        Args:
            entry_price: Entry price
            direction: Trade direction
            indicators: Indicator values

        Returns:
            ExitPlan with all exit levels
        """

        # 1. Detect regime (conservative default if unknown)
        regime = MarketRegime.RANGE
        preset = get_regime_preset(regime, self.config)

        # 2. Calculate stop-loss
        sl_price = self._calculate_stop_loss(
            entry_price=entry_price,
            direction=direction,
            indicators=indicators,
            preset=preset,
        )

        # Calculate SL %
        if direction == "LONG":
            sl_pct = ((entry_price - sl_price) / entry_price) * 100
        else:
            sl_pct = ((sl_price - entry_price) / entry_price) * 100

        # 3. Take-profit (optional)
        tp_enabled = self.config.get("spot", "tp_enabled", default=False)
        tp_price = None
        tp_pct = None

        if tp_enabled:
            tp_pct = self.config.get("spot", "tp_pct", default=3.0)

            if direction == "LONG":
                tp_price = entry_price * (1 + tp_pct / 100)
            else:
                tp_price = entry_price * (1 - tp_pct / 100)

        # Can also use ATR-based TP
        elif self.use_atr_sl and indicators and indicators.atr > 0:
            if direction == "LONG":
                tp_price = entry_price + (indicators.atr * self.atr_tp_multiplier)
            else:
                tp_price = entry_price - (indicators.atr * self.atr_tp_multiplier)

            tp_pct = abs((tp_price - entry_price) / entry_price) * 100

        # 4. Trailing stop settings
        trail_enabled = self.config.get("spot", "trail_enabled", default=True)
        trail_activation_pct = preset.breakeven_trigger_pct
        trail_distance_pct = preset.trailing_stop_pct

        # ATR-based trailing (optional)
        use_atr_trail = self.config.get("spot", "use_atr_trail", default=False)
        if use_atr_trail and indicators and indicators.atr > 0:
            atr_trail_mult = self.config.get(
                "spot", "atr_trail_multiplier", default=1.0
            )
            trail_distance_pct = (indicators.atr * atr_trail_mult / entry_price) * 100

        # 5. Timeout settings
        timeout_minutes = self.config.get("spot", "timeout_minutes", default=60)
        min_profit_on_timeout = self.config.get(
            "spot", "min_profit_to_close_pct", default=0.15
        )

        # 6. Break-even settings
        be_enabled = self.config.get("spot", "be_enabled", default=True)
        be_activation_pct = self.config.get("spot", "be_activation_pct", default=0.5)
        be_offset_pct = self.config.get("spot", "be_offset_pct", default=0.05)

        # 7. Create exit plan
        plan = ExitPlan(
            stop_loss_price=sl_price,
            stop_loss_pct=sl_pct,
            take_profit_price=tp_price,
            take_profit_pct=tp_pct,
            trailing_active=trail_enabled,
            trailing_activation_pct=trail_activation_pct,
            trailing_distance_pct=trail_distance_pct,
            breakeven_enabled=be_enabled,
            breakeven_activation_pct=be_activation_pct,
            breakeven_offset_pct=be_offset_pct,
            timeout_minutes=timeout_minutes,
            min_profit_on_timeout_pct=min_profit_on_timeout,
        )

        return plan

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    def _check_ema_alignment(self, indicators: IndicatorValues) -> Optional[str]:
        """
        Check EMA alignment.

        LONG: EMA9 > EMA21 > EMA50
        SHORT: EMA9 < EMA21 < EMA50 (future)

        Args:
            indicators: Indicator values

        Returns:
            'LONG', 'SHORT', or None
        """

        # LONG condition
        if indicators.ema9 > indicators.ema21 > indicators.ema50:
            return "LONG"

        # SHORT condition (disabled for now)
        # if (indicators.ema9 < indicators.ema21 < indicators.ema50):
        #     return 'SHORT'

        return None

    def _check_rsi(self, indicators: IndicatorValues, direction: str) -> bool:
        """
        Check RSI conditions.

        LONG: RSI between min and max (not overbought)
        SHORT: RSI between (100-max) and (100-min) (not oversold)

        Args:
            indicators: Indicator values
            direction: Trade direction

        Returns:
            True if RSI conditions met
        """

        if direction == "LONG":
            # Not too low (no dead cat bounce), not too high (overbought)
            return self.rsi_min <= indicators.rsi <= self.rsi_max

        else:  # SHORT
            # Inverse for shorts
            return (100 - self.rsi_max) <= indicators.rsi <= (100 - self.rsi_min)

    def _check_volume(self, df: pd.DataFrame) -> bool:
        """
        Check if current volume is above average.

        Logic:
        - Current volume > avg(volume, 20) * multiplier

        Args:
            df: OHLCV dataframe

        Returns:
            True if volume condition met
        """

        if len(df) < 20:
            return True  # Not enough data, assume OK

        current_volume = df["volume"].iloc[-1]
        avg_volume = df["volume"].iloc[-20:].mean()

        return current_volume >= (avg_volume * self.volume_multiplier)

    def _check_di_alignment(self, indicators: IndicatorValues, direction: str) -> bool:
        """
        Check DI (Directional Indicator) alignment with direction.

        LONG: +DI > -DI
        SHORT: -DI > +DI

        Args:
            indicators: Indicator values
            direction: Trade direction

        Returns:
            True if DI aligned with direction
        """

        if direction == "LONG":
            return indicators.di_plus > indicators.di_minus
        else:  # SHORT
            return indicators.di_minus > indicators.di_plus

    def _calculate_stop_loss(
        self,
        entry_price: float,
        direction: str,
        indicators: Optional[IndicatorValues],
        preset: RegimePreset,
    ) -> float:
        """
        Calculate stop-loss price.

        Uses either:
        - Fixed % from regime preset
        - ATR-based (if enabled)

        Args:
            entry_price: Entry price
            direction: Trade direction
            indicators: Indicator values (for ATR)
            preset: Regime preset

        Returns:
            Stop-loss price
        """

        # ATR-based stop loss
        if self.use_atr_sl and indicators and indicators.atr > 0:
            if direction == "LONG":
                sl_price = entry_price - (indicators.atr * self.atr_sl_multiplier)
            else:  # SHORT
                sl_price = entry_price + (indicators.atr * self.atr_sl_multiplier)

        # Fixed % stop loss
        else:
            if direction == "LONG":
                sl_price = entry_price * (1 - preset.stop_loss_pct / 100)
            else:  # SHORT
                sl_price = entry_price * (1 + preset.stop_loss_pct / 100)

        return sl_price

    def _build_signal_notes(
        self, indicators: IndicatorValues, regime: MarketRegime, df: pd.DataFrame
    ) -> str:
        """
        Build human-readable signal notes.

        Args:
            indicators: Indicator values
            regime: Market regime
            df: OHLCV dataframe

        Returns:
            Notes string
        """

        notes = []

        # EMA alignment
        notes.append(f"EMA aligned")

        # ADX strength
        if indicators.adx > self.strong_adx:
            notes.append(f"Strong trend (ADX={indicators.adx:.1f})")
        else:
            notes.append(f"ADX={indicators.adx:.1f}")

        # RSI
        notes.append(f"RSI={indicators.rsi:.1f}")

        # Volume
        current_vol = df["volume"].iloc[-1]
        avg_vol = df["volume"].iloc[-20:].mean() if len(df) >= 20 else current_vol
        vol_ratio = current_vol / avg_vol if avg_vol > 0 else 1.0

        if vol_ratio >= self.volume_spike:
            notes.append(f"Vol spike ({vol_ratio:.1f}x)")
        elif vol_ratio >= self.volume_multiplier:
            notes.append(f"Vol OK ({vol_ratio:.1f}x)")

        # MACD
        if indicators.macd_hist > 0:
            notes.append("MACD↑")
        else:
            notes.append("MACD↓")

        # Regime
        notes.append(f"Regime: {regime.value}")

        return ", ".join(notes)

    def get_min_data_length(self) -> int:
        """
        Minimum candles needed.

        Depends on timeframe (EMA50 + buffer).
        """
        # Base minimum (for 5m)
        base_min = 100

        # Adjust for timeframe
        # Longer timeframes need fewer candles
        # Shorter timeframes need more candles

        # This could be refined based on actual timeframe
        return base_min

    # =========================================================================
    # ADDITIONAL FEATURES
    # =========================================================================

    def supports_timeframe(self, timeframe: str) -> bool:
        """
        Check if strategy supports given timeframe.

        SignalBolt Original works on all timeframes.

        Args:
            timeframe: Timeframe string (1m, 5m, 1h, etc.)

        Returns:
            True if supported
        """
        return True  # Works on all timeframes

    def get_optimal_timeframes(self) -> list[str]:
        """
        Get optimal timeframes for this strategy.

        Returns:
            List of recommended timeframes
        """
        return ["5m", "15m", "1h", "4h"]  # Works best on these

    def __repr__(self) -> str:
        return (
            f"SignalBoltOriginal(timeframe={self.timeframe}, "
            f"min_adx={self.min_adx}, rsi={self.rsi_min}-{self.rsi_max}, "
            f"mtf={self.mtf_enabled})"
        )


# =============================================================================
# STRATEGY VARIANTS
# =============================================================================


class SignalBoltAggressive(SignalBoltOriginal):
    """
    Aggressive variant: lower thresholds, higher risk.

    Differences from Original:
    - Lower ADX threshold (20 vs 25)
    - Higher RSI max (75 vs 70)
    - Lower volume requirement (1.0x vs 1.2x)
    - Higher position sizes
    - Wider stop-loss
    """

    def __init__(self, config: Config):
        super().__init__(config)

        # Override parameters
        self.min_adx = config.get("strategy", "aggressive_min_adx", default=20.0)
        self.rsi_max = config.get("strategy", "aggressive_rsi_max", default=75.0)
        self.volume_multiplier = config.get(
            "strategy", "aggressive_volume_mult", default=1.0
        )

        log.info(
            "SignalBolt Aggressive initialized "
            f"(min_adx={self.min_adx}, rsi_max={self.rsi_max})"
        )


class SignalBoltConservative(SignalBoltOriginal):
    """
    Conservative variant: higher thresholds, lower risk.

    Differences from Original:
    - Higher ADX threshold (30 vs 25)
    - Lower RSI max (65 vs 70)
    - Higher volume requirement (1.5x vs 1.2x)
    - Smaller position sizes
    - Tighter stop-loss
    """

    def __init__(self, config: Config):
        super().__init__(config)

        # Override parameters
        self.min_adx = config.get("strategy", "conservative_min_adx", default=30.0)
        self.rsi_max = config.get("strategy", "conservative_rsi_max", default=65.0)
        self.volume_multiplier = config.get(
            "strategy", "conservative_volume_mult", default=1.5
        )

        log.info(
            "SignalBolt Conservative initialized "
            f"(min_adx={self.min_adx}, rsi_max={self.rsi_max})"
        )


class SignalBoltScalper(SignalBoltOriginal):
    """
    Scalper variant: optimized for 1m-5m timeframes.

    Differences from Original:
    - Designed for 1m/3m/5m
    - Lower ADX requirement (faster signals)
    - Tighter exits
    - Lower profit targets
    - Faster timeout
    """

    def __init__(self, config: Config):
        super().__init__(config)

        # Override for scalping
        self.timeframe = config.get("strategy", "scalper_timeframe", default="1m")
        self.min_adx = config.get("strategy", "scalper_min_adx", default=22.0)
        self.volume_multiplier = config.get(
            "strategy", "scalper_volume_mult", default=1.5
        )

        # Scalper-specific
        self.quick_profit_pct = config.get(
            "strategy", "scalper_quick_profit", default=0.3
        )
        self.max_hold_minutes = config.get("strategy", "scalper_max_hold", default=15)

        log.info(
            "SignalBolt Scalper initialized "
            f"(timeframe={self.timeframe}, quick_profit={self.quick_profit_pct}%)"
        )

    def calculate_exits(
        self, entry_price: float, direction: str, indicators: IndicatorValues
    ) -> ExitPlan:
        """Override exits for scalping strategy."""

        # Get base plan
        plan = super().calculate_exits(entry_price, direction, indicators)

        # Adjust for scalping
        plan.timeout_minutes = self.max_hold_minutes
        plan.min_profit_on_timeout_pct = self.quick_profit_pct
        plan.trailing_distance_pct = 0.2  # Tighter trailing

        return plan

    def get_optimal_timeframes(self) -> list[str]:
        """Scalper optimal timeframes."""
        return ["1m", "3m", "5m"]
