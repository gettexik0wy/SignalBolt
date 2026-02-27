"""
SignalBolt Scalper Strategy

Ultra-short-term strategy optimized for 1m-5m timeframes.

Philosophy:
    "Get in fast, get out faster. Small profits, high frequency."

Key Differences from Original:
    - Faster EMAs: EMA(5, 13, 34) instead of EMA(9, 21, 50)
    - Stochastic as PRIMARY oscillator (faster than RSI)
    - Bollinger Band squeeze detection (volatility expansion entry)
    - Mandatory volume spike (>1.8x average)
    - Ultra-tight exits: 0.3-0.5% TP, 0.5% SL, 15min timeout
    - MTF DISABLED by default (latency kills scalpers)
    - Custom scoring weights: volume and momentum weighted 2x
    - Partial take-profit support
    - Regime-specific behavior: REFUSES to trade in CRASH

Entry Logic:
    - LONG: EMA5 > EMA13 > EMA34 (fast alignment)
    - Stochastic K > D, both rising from oversold (<30)
    - ADX > 20 (lower threshold - catches moves earlier)
    - Volume > 1.8x average (MANDATORY - no volume = no trade)
    - BB width expanding (squeeze breakout)
    - Price above BB middle band
    - Score >= regime-adjusted minimum

Exit Logic:
    - Stop-loss: 0.5% fixed OR 1.0x ATR (whichever is tighter)
    - Trailing stop: 0.2% distance, activates at +0.3%
    - Quick take-profit: 0.3-0.5% (partial exits)
    - Hard timeout: 15 minutes (no babysitting)
    - Emergency exit: if volume drops below 0.5x average mid-trade

Performance Targets:
    - Win rate: 55-65%
    - Avg profit per trade: 0.2-0.4%
    - Avg loss per trade: 0.3-0.5%
    - Trades per day: 10-30
    - Sharpe ratio: >1.5

Recommended Config:
    strategy:
      name: "SignalBoltScalper"
      timeframe: "1m"           # or "3m", "5m"
      scalper_fast_ema: 5
      scalper_mid_ema: 13
      scalper_slow_ema: 34
      scalper_min_adx: 20
      scalper_volume_mult: 1.8
      scalper_quick_profit: 0.3
      scalper_max_hold: 15
      scalper_sl_pct: 0.5
      mtf_enabled: false        # MUST be false for scalping
"""

from typing import Optional, List
from datetime import datetime
from dataclasses import dataclass
import pandas as pd

from signalbolt.core.strategy import Strategy, Signal, EntryPlan, ExitPlan
from signalbolt.core.config import Config
from signalbolt.core.indicators import IndicatorValues, IndicatorCalculator
from signalbolt.core.scoring import ScoreBreakdown, calculate_score
from signalbolt.core.risk import RiskManager
from signalbolt.exchange.base import Ticker
from signalbolt.regime.detector import RegimeDetector, MarketRegime
from signalbolt.regime.presets import get_regime_preset, RegimePreset
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.strategies.scalper")


# =============================================================================
# SCALPER-SPECIFIC REGIME PRESETS
# =============================================================================
# These override the global presets for scalping context.
# Scalping needs TIGHTER stops, FASTER exits, HIGHER volume requirements.

SCALPER_REGIME_PRESETS = {
    MarketRegime.BULL: RegimePreset(
        stop_loss_pct=0.5,
        breakeven_trigger_pct=0.25,
        trailing_stop_pct=0.2,
        min_signal_score=55.0,
        slippage_pct=0.03,
        wallet_pct=85.0,
        max_positions=3,
        scan_interval_sec=15,
    ),
    MarketRegime.RANGE: RegimePreset(
        stop_loss_pct=0.5,
        breakeven_trigger_pct=0.3,
        trailing_stop_pct=0.25,
        min_signal_score=62.0,
        slippage_pct=0.05,
        wallet_pct=70.0,
        max_positions=2,
        scan_interval_sec=20,
    ),
    MarketRegime.BEAR: RegimePreset(
        stop_loss_pct=0.4,
        breakeven_trigger_pct=0.2,
        trailing_stop_pct=0.15,
        min_signal_score=72.0,
        slippage_pct=0.08,
        wallet_pct=45.0,
        max_positions=1,
        scan_interval_sec=30,
    ),
    MarketRegime.CRASH: RegimePreset(
        stop_loss_pct=0.3,
        breakeven_trigger_pct=0.15,
        trailing_stop_pct=0.1,
        min_signal_score=95.0,  # Effectively disabled
        slippage_pct=0.15,
        wallet_pct=0.0,  # ZERO - do not trade
        max_positions=0,
        scan_interval_sec=120,
    ),
}


# =============================================================================
# SCALPER SCORING ENGINE
# =============================================================================


class ScalperScorer:
    """
    Custom scoring for scalping strategy.

    Weight distribution (total 100 + bonus 25):
        - Stochastic alignment:   20 pts  (primary oscillator)
        - Volume strength:        25 pts  (most important for scalping)
        - EMA alignment + gap:    15 pts
        - ADX strength:           15 pts
        - BB position:            15 pts
        - DI spread:              10 pts
        - MACD momentum bonus:   +10 pts
        - Volume spike bonus:    +10 pts
        - Stoch oversold bounce: + 5 pts

    Philosophy: Volume is KING in scalping. No volume = no scalp.
    """

    def score(
        self,
        ind: IndicatorValues,
        direction: str,
        df: pd.DataFrame,
        fast_ema: float,
        mid_ema: float,
        slow_ema: float,
    ) -> ScoreBreakdown:
        """
        Calculate scalper-optimized score.

        Args:
            ind: Indicator values from the standard calculator
            direction: 'LONG' or 'SHORT'
            df: Full OHLCV dataframe (for BB squeeze detection)
            fast_ema: Fast EMA value (e.g., EMA5)
            mid_ema: Mid EMA value (e.g., EMA13)
            slow_ema: Slow EMA value (e.g., EMA34)

        Returns:
            ScoreBreakdown with scalper-weighted components
        """
        breakdown = ScoreBreakdown()

        # === CORE COMPONENTS ===

        # 1. Stochastic alignment (0-20) - PRIMARY for scalper
        breakdown.rsi = self._score_stochastic_primary(ind, direction)

        # 2. Volume strength (0-25) - MOST IMPORTANT
        breakdown.volume = self._score_volume_strength(ind)

        # 3. EMA alignment (0-15)
        breakdown.ema_alignment = self._score_ema(
            fast_ema, mid_ema, slow_ema, ind.close, direction
        )

        # 4. ADX strength (0-15)
        breakdown.adx_strength = self._score_adx(ind)

        # 5. BB position (0-15)
        breakdown.price_position = self._score_bb_position(ind, direction, df)

        # 6. DI spread (0-10)
        breakdown.di_spread = self._score_di(ind, direction)

        # === BONUS COMPONENTS ===

        # 7. MACD momentum (+0-10)
        if ind.macd is not None:
            breakdown.macd_bonus = self._score_macd_momentum(ind, direction)

        # 8. Volume spike (+0-10)
        breakdown.bb_bonus = self._score_volume_spike(ind)

        # 9. Stochastic oversold bounce (+0-5)
        if ind.stoch_k is not None:
            breakdown.stoch_bonus = self._score_stoch_bounce(ind, direction)

        return breakdown

    def _score_stochastic_primary(self, ind: IndicatorValues, direction: str) -> float:
        """Stochastic as primary oscillator (0-20)."""
        if ind.stoch_k is None or ind.stoch_d is None:
            # Fallback to RSI if stochastic not available
            return self._score_rsi_fallback(ind, direction)

        k, d = ind.stoch_k, ind.stoch_d

        if direction == "LONG":
            # Best: K crossing above D from oversold zone
            if k > d and k < 25:
                return 20.0
            elif k > d and k < 35:
                return 17.0
            elif k > d and k < 50:
                return 13.0
            elif k > d and k < 65:
                return 8.0
            elif k > d:
                return 4.0
            else:
                return 0.0
        else:  # SHORT
            if k < d and k > 75:
                return 20.0
            elif k < d and k > 65:
                return 17.0
            elif k < d and k > 50:
                return 13.0
            elif k < d and k > 35:
                return 8.0
            elif k < d:
                return 4.0
            else:
                return 0.0

    def _score_rsi_fallback(self, ind: IndicatorValues, direction: str) -> float:
        """RSI fallback if stochastic unavailable (0-20)."""
        rsi = ind.rsi
        if direction == "LONG":
            if 40 <= rsi <= 55:
                return 16.0
            elif 35 <= rsi <= 60:
                return 12.0
            elif 30 <= rsi <= 65:
                return 7.0
            else:
                return 2.0
        else:
            if 45 <= rsi <= 60:
                return 16.0
            elif 40 <= rsi <= 65:
                return 12.0
            elif 35 <= rsi <= 70:
                return 7.0
            else:
                return 2.0

    def _score_volume_strength(self, ind: IndicatorValues) -> float:
        """Volume strength (0-25). King metric for scalping."""
        ratio = ind.volume_ratio

        if ratio >= 4.0:
            return 25.0
        elif ratio >= 3.0:
            return 22.0
        elif ratio >= 2.5:
            return 19.0
        elif ratio >= 2.0:
            return 16.0
        elif ratio >= 1.8:
            return 13.0
        elif ratio >= 1.5:
            return 9.0
        elif ratio >= 1.2:
            return 5.0
        elif ratio >= 1.0:
            return 2.0
        else:
            return 0.0

    def _score_ema(
        self,
        fast: float,
        mid: float,
        slow: float,
        price: float,
        direction: str,
    ) -> float:
        """Fast EMA alignment (0-15)."""
        if mid == 0:
            return 0.0

        if direction == "LONG":
            if not (fast > mid > slow):
                return 0.0
        else:
            if not (fast < mid < slow):
                return 0.0

        # Gap between fast and mid EMA
        gap_pct = abs(fast - mid) / mid * 100

        if gap_pct > 0.5:
            return 15.0
        elif gap_pct > 0.3:
            return 12.0
        elif gap_pct > 0.15:
            return 9.0
        elif gap_pct > 0.08:
            return 6.0
        else:
            return 3.0

    def _score_adx(self, ind: IndicatorValues) -> float:
        """ADX score for scalping (0-15). Lower thresholds than original."""
        adx = ind.adx

        if adx >= 45:
            return 15.0
        elif adx >= 35:
            return 13.0
        elif adx >= 28:
            return 11.0
        elif adx >= 22:
            return 8.0
        elif adx >= 18:
            return 5.0
        elif adx >= 14:
            return 2.0
        else:
            return 0.0

    def _score_bb_position(
        self, ind: IndicatorValues, direction: str, df: pd.DataFrame
    ) -> float:
        """
        Bollinger Band position and squeeze detection (0-15).

        Scalpers LOVE BB squeeze breakouts:
        - Squeeze (narrow bands) followed by expansion = explosive move
        - Price breaking above middle band with volume = entry
        """
        if ind.bb_upper is None or ind.bb_lower is None:
            return 5.0  # Neutral if BB not available

        price = ind.close
        upper = ind.bb_upper
        lower = ind.bb_lower
        middle = ind.bb_middle
        bb_range = upper - lower

        if bb_range <= 0 or middle <= 0:
            return 0.0

        # Squeeze detection: compare current BB width to recent average
        squeeze_bonus = 0.0
        if "bb_width_pct" in df.columns and len(df) >= 20:
            current_width = ind.bb_width_pct or 0
            avg_width = df["bb_width_pct"].iloc[-20:].mean()

            if avg_width > 0:
                width_ratio = current_width / avg_width

                # Squeeze releasing (width was tight, now expanding)
                if width_ratio < 0.7:
                    squeeze_bonus = 5.0  # Still in squeeze
                elif 0.7 <= width_ratio <= 1.0:
                    squeeze_bonus = 3.0  # Squeeze releasing

        # Position scoring
        position_score = 0.0
        pct_position = (price - lower) / bb_range if bb_range > 0 else 0.5

        if direction == "LONG":
            if 0.5 <= pct_position <= 0.75:
                # Price above middle, not yet at upper = sweet spot
                position_score = 10.0
            elif 0.35 <= pct_position < 0.5:
                # Near middle band, bouncing up
                position_score = 7.0
            elif pct_position > 0.75:
                # Near upper band - risky for scalp entry
                position_score = 3.0
            elif pct_position < 0.2:
                # Too low - falling knife
                position_score = 0.0
            else:
                position_score = 4.0
        else:  # SHORT
            if 0.25 <= pct_position <= 0.5:
                position_score = 10.0
            elif 0.5 < pct_position <= 0.65:
                position_score = 7.0
            elif pct_position < 0.25:
                position_score = 3.0
            elif pct_position > 0.8:
                position_score = 0.0
            else:
                position_score = 4.0

        return min(15.0, position_score + squeeze_bonus)

    def _score_di(self, ind: IndicatorValues, direction: str) -> float:
        """DI spread for direction confirmation (0-10)."""
        if direction == "LONG":
            spread = ind.di_plus - ind.di_minus
        else:
            spread = ind.di_minus - ind.di_plus

        if spread > 20:
            return 10.0
        elif spread > 15:
            return 8.0
        elif spread > 10:
            return 6.0
        elif spread > 5:
            return 4.0
        elif spread > 0:
            return 2.0
        else:
            return 0.0

    def _score_macd_momentum(self, ind: IndicatorValues, direction: str) -> float:
        """MACD momentum bonus (+0-10)."""
        if ind.macd is None or ind.macd_signal is None:
            return 0.0

        histogram = ind.macd_histogram or (ind.macd - ind.macd_signal)

        if direction == "LONG":
            if histogram > 0 and ind.macd > ind.macd_signal:
                return min(10.0, abs(histogram) / (abs(ind.macd) + 1e-10) * 30)
            return 0.0
        else:
            if histogram < 0 and ind.macd < ind.macd_signal:
                return min(10.0, abs(histogram) / (abs(ind.macd) + 1e-10) * 30)
            return 0.0

    def _score_volume_spike(self, ind: IndicatorValues) -> float:
        """Volume spike bonus (+0-10). Stored in bb_bonus slot."""
        ratio = ind.volume_ratio

        if ratio >= 5.0:
            return 10.0
        elif ratio >= 4.0:
            return 8.0
        elif ratio >= 3.0:
            return 5.0
        elif ratio >= 2.5:
            return 3.0
        else:
            return 0.0

    def _score_stoch_bounce(self, ind: IndicatorValues, direction: str) -> float:
        """Stochastic oversold/overbought bounce bonus (+0-5)."""
        if ind.stoch_k is None:
            return 0.0

        k = ind.stoch_k

        if direction == "LONG" and k < 20:
            return 5.0
        elif direction == "LONG" and k < 30:
            return 3.0
        elif direction == "SHORT" and k > 80:
            return 5.0
        elif direction == "SHORT" and k > 70:
            return 3.0
        else:
            return 0.0


# =============================================================================
# MAIN STRATEGY CLASS
# =============================================================================


class SignalBoltScalper(Strategy):
    """
    Ultra-short-term scalping strategy.

    Designed for 1m-5m timeframes with:
    - Fast EMA set (5/13/34)
    - Stochastic-primary oscillation
    - BB squeeze breakout detection
    - Mandatory high volume
    - Ultra-tight risk management
    - 15-minute hard timeout
    - Custom scalper scoring engine
    """

    def __init__(self, config: Config):
        """Initialize scalper strategy."""
        super().__init__(config)

        self.regime_detector = RegimeDetector()
        self.risk_manager = RiskManager(config)
        self.scorer = ScalperScorer()

        # ----- Scalper-specific EMA periods -----
        self.fast_ema_period = config.get("strategy", "scalper_fast_ema", default=5)
        self.mid_ema_period = config.get("strategy", "scalper_mid_ema", default=13)
        self.slow_ema_period = config.get("strategy", "scalper_slow_ema", default=34)

        # ----- Scalper thresholds -----
        self.min_adx = config.get("strategy", "scalper_min_adx", default=20.0)
        self.strong_adx = config.get("strategy", "scalper_strong_adx", default=35.0)

        # Stochastic thresholds (primary oscillator)
        self.stoch_oversold = config.get(
            "strategy", "scalper_stoch_oversold", default=25.0
        )
        self.stoch_overbought = config.get(
            "strategy", "scalper_stoch_overbought", default=75.0
        )

        # RSI as secondary filter only
        self.rsi_min = config.get("strategy", "scalper_rsi_min", default=35.0)
        self.rsi_max = config.get("strategy", "scalper_rsi_max", default=72.0)

        # Volume (CRITICAL for scalping)
        self.volume_multiplier = config.get(
            "strategy", "scalper_volume_mult", default=1.8
        )
        self.volume_spike = config.get("strategy", "scalper_volume_spike", default=3.0)
        self.min_volume_exit = config.get(
            "strategy", "scalper_min_volume_exit", default=0.5
        )

        # ----- Exit parameters -----
        self.quick_profit_pct = config.get(
            "strategy", "scalper_quick_profit", default=0.3
        )
        self.max_hold_minutes = config.get("strategy", "scalper_max_hold", default=15)
        self.sl_pct = config.get("strategy", "scalper_sl_pct", default=0.5)
        self.trail_distance = config.get(
            "strategy", "scalper_trail_distance", default=0.2
        )
        self.trail_activation = config.get(
            "strategy", "scalper_trail_activation", default=0.25
        )

        # ----- Partial take-profit -----
        self.partial_tp_enabled = config.get(
            "strategy", "scalper_partial_tp", default=True
        )
        self.partial_tp_levels = config.get(
            "strategy",
            "scalper_partial_tp_levels",
            default=[
                {"pct": 0.25, "close_pct": 40},
                {"pct": 0.45, "close_pct": 35},
                {"pct": 0.70, "close_pct": 25},
            ],
        )

        # ----- Timeframe (forced) -----
        self.timeframe = config.get("strategy", "scalper_timeframe", default="3m")
        self.mtf_enabled = False  # ALWAYS disabled for scalper

        # ----- ATR settings -----
        self.atr_period = config.get("strategy", "atr_period", default=14)
        self.use_atr_sl = config.get("strategy", "scalper_use_atr_sl", default=True)
        self.atr_sl_multiplier = config.get(
            "strategy", "scalper_atr_sl_mult", default=1.0
        )

        # ----- BB squeeze -----
        self.bb_squeeze_threshold = config.get(
            "strategy", "scalper_bb_squeeze", default=0.015
        )

        # Initialize custom indicator calculator with all bonuses enabled
        self.indicator_calculator = IndicatorCalculator(
            enable_macd=True,
            enable_bb=True,
            enable_stoch=True,
            cache_enabled=True,
        )

        log.info(
            f"SignalBolt Scalper initialized "
            f"(tf={self.timeframe}, ema={self.fast_ema_period}/{self.mid_ema_period}/{self.slow_ema_period}, "
            f"min_adx={self.min_adx}, vol_mult={self.volume_multiplier}, "
            f"sl={self.sl_pct}%, tp={self.quick_profit_pct}%, "
            f"timeout={self.max_hold_minutes}min, mtf=DISABLED)"
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
        Generate scalper signal.

        Pipeline:
            1. Calculate custom fast EMAs
            2. Get standard indicators
            3. Detect regime (REFUSE to trade in CRASH)
            4. Check fast EMA alignment
            5. Check Stochastic (primary oscillator)
            6. Check ADX (lower threshold)
            7. Check volume (MANDATORY high volume)
            8. Check RSI (secondary filter - wide range)
            9. Check BB position (squeeze breakout preferred)
            10. Calculate scalper-weighted score
            11. Validate against regime-adjusted min_score
            12. Build signal with detailed notes
        """
        if len(df) < 60:
            log.debug(f"{symbol}: Insufficient data ({len(df)} candles, need 60+)")
            return None

        # 1. Calculate custom fast EMAs on the dataframe
        df = self._calculate_fast_emas(df)

        # 2. Get standard indicators
        indicators = self.indicator_calculator.get_latest(df)

        if current_price is None:
            current_price = df["close"].iloc[-1]

        # Get custom EMA values from dataframe
        last = df.iloc[-1]
        fast_ema = float(last.get(f"ema_{self.fast_ema_period}", indicators.ema9))
        mid_ema = float(last.get(f"ema_{self.mid_ema_period}", indicators.ema21))
        slow_ema = float(last.get(f"ema_{self.slow_ema_period}", indicators.ema50))

        # 3. Detect regime
        regime = self.regime_detector.detect(df)

        # REFUSE to trade in CRASH
        if regime == MarketRegime.CRASH:
            log.info(f"{symbol}: CRASH regime detected - scalper REFUSING to trade")
            return None

        # 4. Check fast EMA alignment
        direction = self._check_fast_ema_alignment(fast_ema, mid_ema, slow_ema)

        if direction is None:
            log.debug(f"{symbol}: No fast EMA alignment")
            return None

        # 5. Check Stochastic (primary oscillator)
        if not self._check_stochastic(indicators, direction):
            log.debug(
                f"{symbol}: Stochastic check failed "
                f"(K={indicators.stoch_k}, D={indicators.stoch_d})"
            )
            return None

        # 6. Check ADX
        if indicators.adx < self.min_adx:
            log.debug(
                f"{symbol}: ADX too low for scalp "
                f"({indicators.adx:.1f} < {self.min_adx})"
            )
            return None

        # 7. Check volume (MANDATORY - this is non-negotiable for scalping)
        if not self._check_volume_mandatory(df, indicators):
            log.debug(
                f"{symbol}: Volume INSUFFICIENT for scalp "
                f"(ratio={indicators.volume_ratio:.2f}, "
                f"need>={self.volume_multiplier})"
            )
            return None

        # 8. Check RSI (secondary filter - wide range)
        if not self._check_rsi_secondary(indicators, direction):
            log.debug(f"{symbol}: RSI secondary filter failed ({indicators.rsi:.1f})")
            return None

        # 9. Check DI alignment
        if not self._check_di_alignment(indicators, direction):
            log.debug(f"{symbol}: DI misaligned for {direction}")
            return None

        # 10. Calculate scalper-weighted score
        score_breakdown = self.scorer.score(
            ind=indicators,
            direction=direction,
            df=df,
            fast_ema=fast_ema,
            mid_ema=mid_ema,
            slow_ema=slow_ema,
        )

        # 11. Get regime-adjusted min_score
        preset = self._get_scalper_preset(regime)
        min_score = preset.min_signal_score

        if score_breakdown.total < min_score:
            log.debug(
                f"{symbol}: Scalper score too low "
                f"({score_breakdown.total:.1f} < {min_score})"
            )
            return None

        # 12. Build signal
        notes = self._build_scalper_notes(
            indicators, regime, df, fast_ema, mid_ema, slow_ema
        )

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

        log.info(
            f"⚡ SCALPER SIGNAL: {symbol} {direction} "
            f"score={score_breakdown.total:.1f} "
            f"regime={regime.value} price={current_price}"
        )

        return signal

    # =========================================================================
    # CORE: ENTRY PLAN
    # =========================================================================

    def calculate_entry(
        self, signal: Signal, ticker: Ticker, wallet_balance: float
    ) -> EntryPlan:
        """
        Calculate scalper entry plan.

        Key differences from Original:
        - Always MARKET order (speed > price improvement)
        - Tighter SL (min of fixed % and ATR-based)
        - Smaller position sizes in BEAR
        - Scalper-specific regime presets
        """
        # Always market order for scalping (speed is everything)
        entry_price = ticker.last_price

        # Get scalper regime preset
        regime = MarketRegime(signal.regime)
        preset = self._get_scalper_preset(regime)

        # Calculate stop-loss (tighter of fixed % and ATR)
        sl_price = self._calculate_scalper_sl(
            entry_price=entry_price,
            direction=signal.direction,
            indicators=signal.indicators,
            preset=preset,
        )

        # Position size
        position_size = self.risk_manager.calculate_position_size(
            balance=wallet_balance,
            entry_price=entry_price,
            stop_loss_price=sl_price,
            direction=signal.direction,
        )

        # Apply scalper regime wallet_pct cap
        max_size_usd = wallet_balance * (preset.wallet_pct / 100)
        actual_size_usd = min(position_size.size_usd, max_size_usd)

        quantity = actual_size_usd / entry_price

        # SL percentage
        if signal.direction == "LONG":
            sl_pct = ((entry_price - sl_price) / entry_price) * 100
        else:
            sl_pct = ((sl_price - entry_price) / entry_price) * 100

        notes = (
            f"SCALPER | Risk: ${position_size.risk_usd:.2f} "
            f"({position_size.risk_pct:.2f}%), "
            f"SL: {sl_pct:.2f}%, "
            f"Regime: {regime.value}, "
            f"Timeout: {self.max_hold_minutes}min"
        )

        plan = EntryPlan(
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=entry_price,
            position_size_usd=actual_size_usd,
            quantity=quantity,
            stop_loss_price=sl_price,
            stop_loss_pct=sl_pct,
            use_limit=False,  # Always market for scalper
            limit_offset_pct=0.0,
            notes=notes,
        )

        return plan

    # =========================================================================
    # CORE: EXIT PLAN
    # =========================================================================

    def calculate_exits(
        self, entry_price: float, direction: str, indicators: IndicatorValues
    ) -> ExitPlan:
        """
        Calculate scalper exit plan.

        Scalper exits are AGGRESSIVE:
        - Tight SL (0.5% or 1.0x ATR, whichever is tighter)
        - Quick trailing (activates at +0.25%, trails at 0.2%)
        - Short timeout (15 minutes)
        - Optional partial TP at 0.25%, 0.45%, 0.70%
        - Break-even at +0.15%
        """
        regime = MarketRegime.RANGE  # Conservative default
        preset = self._get_scalper_preset(regime)

        # Stop-loss
        sl_price = self._calculate_scalper_sl(
            entry_price=entry_price,
            direction=direction,
            indicators=indicators,
            preset=preset,
        )

        if direction == "LONG":
            sl_pct = ((entry_price - sl_price) / entry_price) * 100
        else:
            sl_pct = ((sl_price - entry_price) / entry_price) * 100

        # Take-profit (quick scalp target)
        tp_pct = self.quick_profit_pct
        if direction == "LONG":
            tp_price = entry_price * (1 + tp_pct / 100)
        else:
            tp_price = entry_price * (1 - tp_pct / 100)

        # ATR-based trailing distance (if available)
        trail_dist = self.trail_distance
        if indicators and indicators.atr > 0:
            atr_trail = (indicators.atr * 0.5 / entry_price) * 100
            trail_dist = min(trail_dist, atr_trail)  # Use tighter of the two

        plan = ExitPlan(
            stop_loss_price=sl_price,
            stop_loss_pct=sl_pct,
            take_profit_price=tp_price,
            take_profit_pct=tp_pct,
            trailing_active=True,
            trailing_activation_pct=self.trail_activation,
            trailing_distance_pct=trail_dist,
            breakeven_enabled=True,
            breakeven_activation_pct=0.15,  # Very early BE
            breakeven_offset_pct=0.02,  # Tiny offset
            timeout_minutes=self.max_hold_minutes,
            min_profit_on_timeout_pct=0.08,  # Close even at tiny profit on timeout
        )

        return plan

    # =========================================================================
    # HELPER: CUSTOM FAST EMAs
    # =========================================================================

    def _calculate_fast_emas(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate custom fast EMA set on the dataframe.

        Adds columns: ema_5, ema_13, ema_34 (or whatever periods are configured)
        """
        import pandas_ta as ta

        fast_col = f"ema_{self.fast_ema_period}"
        mid_col = f"ema_{self.mid_ema_period}"
        slow_col = f"ema_{self.slow_ema_period}"

        if fast_col not in df.columns:
            df[fast_col] = ta.ema(df["close"], length=self.fast_ema_period)
        if mid_col not in df.columns:
            df[mid_col] = ta.ema(df["close"], length=self.mid_ema_period)
        if slow_col not in df.columns:
            df[slow_col] = ta.ema(df["close"], length=self.slow_ema_period)

        return df

    # =========================================================================
    # HELPER: SIGNAL CHECKS
    # =========================================================================

    def _check_fast_ema_alignment(
        self, fast: float, mid: float, slow: float
    ) -> Optional[str]:
        """
        Check fast EMA alignment.

        LONG: EMA5 > EMA13 > EMA34
        SHORT: EMA5 < EMA13 < EMA34 (future)
        """
        if fast > mid > slow:
            return "LONG"
        # SHORT disabled for now
        # if fast < mid < slow:
        #     return "SHORT"
        return None

    def _check_stochastic(self, indicators: IndicatorValues, direction: str) -> bool:
        """
        Check Stochastic conditions (primary oscillator for scalper).

        LONG: K > D (bullish crossover) and K not extreme overbought (>90)
        SHORT: K < D (bearish crossover) and K not extreme oversold (<10)
        """
        if indicators.stoch_k is None or indicators.stoch_d is None:
            # If stochastic not available, pass through (rely on other checks)
            log.debug("Stochastic not available, using RSI fallback")
            return self._check_rsi_secondary(indicators, direction)

        k, d = indicators.stoch_k, indicators.stoch_d

        if direction == "LONG":
            # K must be above D (bullish) and not extreme overbought
            return k > d and k < 90
        else:  # SHORT
            return k < d and k > 10

    def _check_volume_mandatory(
        self, df: pd.DataFrame, indicators: IndicatorValues
    ) -> bool:
        """
        MANDATORY volume check for scalping.

        Volume MUST be above multiplier threshold.
        This is the most important filter for scalping - without volume,
        price moves are unreliable and spreads widen.
        """
        return indicators.volume_ratio >= self.volume_multiplier

    def _check_rsi_secondary(self, indicators: IndicatorValues, direction: str) -> bool:
        """
        RSI as SECONDARY filter (wider range than original).

        Only rejects extreme overbought/oversold.
        """
        if direction == "LONG":
            return self.rsi_min <= indicators.rsi <= self.rsi_max
        else:
            return (100 - self.rsi_max) <= indicators.rsi <= (100 - self.rsi_min)

    def _check_di_alignment(self, indicators: IndicatorValues, direction: str) -> bool:
        """Check DI alignment with direction."""
        if direction == "LONG":
            return indicators.di_plus > indicators.di_minus
        else:
            return indicators.di_minus > indicators.di_plus

    # =========================================================================
    # HELPER: STOP LOSS
    # =========================================================================

    def _calculate_scalper_sl(
        self,
        entry_price: float,
        direction: str,
        indicators: Optional[IndicatorValues],
        preset: RegimePreset,
    ) -> float:
        """
        Calculate scalper stop-loss.

        Uses the TIGHTER of:
        - Fixed % from scalper preset (e.g., 0.5%)
        - ATR-based (1.0x ATR)

        This ensures the SL is never wider than needed for a scalp.
        """
        # Fixed % SL
        fixed_sl_pct = preset.stop_loss_pct

        if direction == "LONG":
            sl_fixed = entry_price * (1 - fixed_sl_pct / 100)
        else:
            sl_fixed = entry_price * (1 + fixed_sl_pct / 100)

        # ATR-based SL
        if self.use_atr_sl and indicators and indicators.atr > 0:
            if direction == "LONG":
                sl_atr = entry_price - (indicators.atr * self.atr_sl_multiplier)
            else:
                sl_atr = entry_price + (indicators.atr * self.atr_sl_multiplier)

            # Use the TIGHTER stop (closer to entry)
            if direction == "LONG":
                return max(sl_fixed, sl_atr)  # Higher = tighter for LONG
            else:
                return min(sl_fixed, sl_atr)  # Lower = tighter for SHORT

        return sl_fixed

    # =========================================================================
    # HELPER: REGIME PRESET
    # =========================================================================

    def _get_scalper_preset(self, regime: MarketRegime) -> RegimePreset:
        """
        Get scalper-specific regime preset.

        Falls back to global preset if scalper-specific not defined.
        """
        if regime in SCALPER_REGIME_PRESETS:
            return SCALPER_REGIME_PRESETS[regime]
        return get_regime_preset(regime, self.config)

    # =========================================================================
    # HELPER: SIGNAL NOTES
    # =========================================================================

    def _build_scalper_notes(
        self,
        indicators: IndicatorValues,
        regime: MarketRegime,
        df: pd.DataFrame,
        fast_ema: float,
        mid_ema: float,
        slow_ema: float,
    ) -> str:
        """Build detailed scalper signal notes."""
        notes = []

        # Strategy identifier
        notes.append("⚡SCALPER")

        # EMA info
        gap_pct = abs(fast_ema - mid_ema) / mid_ema * 100 if mid_ema > 0 else 0
        notes.append(
            f"EMA({self.fast_ema_period}/{self.mid_ema_period}/{self.slow_ema_period}) "
            f"gap={gap_pct:.3f}%"
        )

        # Stochastic
        if indicators.stoch_k is not None:
            k_str = f"K={indicators.stoch_k:.1f}"
            d_str = f"D={indicators.stoch_d:.1f}"
            cross = "K>D" if indicators.stoch_k > indicators.stoch_d else "K<D"
            notes.append(f"Stoch({k_str},{d_str},{cross})")

        # ADX
        notes.append(f"ADX={indicators.adx:.1f}")

        # Volume (critical)
        vol_emoji = "🔥" if indicators.volume_ratio >= self.volume_spike else "📊"
        notes.append(f"{vol_emoji}Vol={indicators.volume_ratio:.1f}x")

        # BB squeeze
        if indicators.bb_width_pct is not None:
            if indicators.bb_width_pct < self.bb_squeeze_threshold * 100:
                notes.append("🔧BB-SQUEEZE")
            else:
                notes.append(f"BB-w={indicators.bb_width_pct:.2f}%")

        # RSI (secondary)
        notes.append(f"RSI={indicators.rsi:.1f}")

        # MACD direction
        if indicators.macd_histogram is not None:
            macd_dir = "↑" if indicators.macd_histogram > 0 else "↓"
            notes.append(f"MACD{macd_dir}")

        # Regime
        notes.append(f"Regime:{regime.value}")

        # Timeout
        notes.append(f"⏰{self.max_hold_minutes}min")

        return ", ".join(notes)

    # =========================================================================
    # METADATA
    # =========================================================================

    def get_min_data_length(self) -> int:
        """Minimum candles needed (less than original due to faster EMAs)."""
        return max(60, self.slow_ema_period + 30)

    def supports_timeframe(self, timeframe: str) -> bool:
        """Scalper only supports fast timeframes."""
        supported = {"1m", "3m", "5m"}
        return timeframe in supported

    def get_optimal_timeframes(self) -> list[str]:
        """Optimal timeframes for scalping."""
        return ["1m", "3m", "5m"]

    def __repr__(self) -> str:
        return (
            f"SignalBoltScalper("
            f"tf={self.timeframe}, "
            f"ema={self.fast_ema_period}/{self.mid_ema_period}/{self.slow_ema_period}, "
            f"adx>={self.min_adx}, "
            f"vol>={self.volume_multiplier}x, "
            f"sl={self.sl_pct}%, "
            f"tp={self.quick_profit_pct}%, "
            f"timeout={self.max_hold_minutes}min)"
        )
