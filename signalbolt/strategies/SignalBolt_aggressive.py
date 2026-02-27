"""
SignalBolt Aggressive Strategy

High-frequency, high-risk strategy maximizing trade opportunities.

Philosophy:
    "Cast a wide net, manage risk with position sizing and trailing stops."

Key Differences from Original:
    - Lower ADX threshold (18 vs 25) - catches trends EARLIER
    - Wider RSI window (35-78 for LONG) - accepts more momentum
    - Lower volume requirement (0.8x) - trades in quieter conditions
    - Higher position sizes (Kelly-aware)
    - Earlier trailing stop activation (+0.3%)
    - Longer timeout (120 min) - lets winners run
    - MACD crossover acts as STANDALONE entry signal (can bypass weak ADX)
    - Volume spike bypass: if volume > 3x, lowers all thresholds
    - Regime-aggressive: in BULL, drops min_score to 50
    - DOES trade in BEAR (cautiously) and reduces in CRASH (not disabled)

Entry Logic:
    PRIMARY PATH (standard):
    - LONG: EMA9 > EMA21 > EMA50
    - ADX > 18 (very low - catches early trends)
    - RSI between 35-78 (wide window)
    - Volume >= 0.8x average (accepts below-average volume)
    - DI+ > DI-
    - Score >= regime-adjusted minimum (lower than Original)

    ALTERNATIVE PATH (MACD crossover override):
    - If MACD histogram JUST turned positive (cross in last 3 bars)
    - AND volume > 1.5x
    - AND EMA9 > EMA21 (at least partial alignment)
    - => Accept signal even if ADX < threshold

    VOLUME SPIKE PATH:
    - If volume > 3.0x average
    - Lower min_adx by 5 points
    - Widen RSI window by 5 points
    - Lower min_score by 10 points

Exit Logic:
    - Stop-loss: 2.0% fixed (wider than Original)
    - Trailing stop: 0.4% distance, activates at +0.3%
    - Break-even: at +0.4%, offset 0.05%
    - Timeout: 120 minutes
    - Take-profit: optional, 3.0% if enabled
    - NO emergency exit on ADX drop (unlike conservative)

Performance Targets:
    - Win rate: 48-55%
    - Avg profit per trade: 0.5-1.0%
    - Avg loss per trade: 0.8-1.5%
    - Trades per day: 8-20
    - Sharpe ratio: >1.0
    - Max drawdown: <15%

Recommended Config:
    strategy:
      name: "SignalBoltAggressive"
      timeframe: "5m"
      aggressive_min_adx: 18
      aggressive_rsi_min: 35
      aggressive_rsi_max: 78
      aggressive_volume_mult: 0.8
      aggressive_macd_override: true
      aggressive_volume_spike_bypass: true
"""

from typing import Optional
from datetime import datetime
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

log = get_logger("signalbolt.strategies.aggressive")


# =============================================================================
# AGGRESSIVE REGIME PRESETS
# =============================================================================
# Wider stops, larger positions, lower score requirements.

AGGRESSIVE_REGIME_PRESETS = {
    MarketRegime.BULL: RegimePreset(
        stop_loss_pct=1.8,
        breakeven_trigger_pct=0.3,
        trailing_stop_pct=0.4,
        min_signal_score=50.0,  # Very low - catches everything
        slippage_pct=0.05,
        wallet_pct=90.0,  # Maximum exposure
        max_positions=5,
        scan_interval_sec=30,  # Very frequent scanning
    ),
    MarketRegime.RANGE: RegimePreset(
        stop_loss_pct=2.0,
        breakeven_trigger_pct=0.4,
        trailing_stop_pct=0.45,
        min_signal_score=58.0,
        slippage_pct=0.08,
        wallet_pct=75.0,
        max_positions=4,
        scan_interval_sec=45,
    ),
    MarketRegime.BEAR: RegimePreset(
        stop_loss_pct=2.5,
        breakeven_trigger_pct=0.5,
        trailing_stop_pct=0.6,
        min_signal_score=68.0,
        slippage_pct=0.12,
        wallet_pct=55.0,
        max_positions=2,
        scan_interval_sec=120,
    ),
    MarketRegime.CRASH: RegimePreset(
        stop_loss_pct=3.0,
        breakeven_trigger_pct=0.8,
        trailing_stop_pct=0.8,
        min_signal_score=78.0,  # Still trades, but selective
        slippage_pct=0.20,
        wallet_pct=25.0,  # Reduced but NOT zero
        max_positions=1,
        scan_interval_sec=300,
    ),
}


# =============================================================================
# AGGRESSIVE SCORING ENGINE
# =============================================================================


class AggressiveScorer:
    """
    Aggressive scoring with opportunity-seeking bias.

    Weight distribution (total 100 + bonus 35):
        - EMA alignment:              15 pts
        - ADX strength:               20 pts  (lower thresholds)
        - RSI range:                  12 pts  (wider acceptance)
        - Volume:                     18 pts
        - Price position:             10 pts
        - DI spread:                   5 pts
        - Momentum assessment:        20 pts  (UNIQUE - replaces gap)
        - MACD crossover bonus:      +15 pts  (higher than other variants)
        - Volume spike bonus:        +10 pts
        - Stochastic bonus:          +10 pts

    "Momentum Assessment" is UNIQUE to aggressive:
        - Combines price change rate, RSI trend, and MACD acceleration
        - Rewards MOMENTUM over stability
    """

    def score(
        self,
        ind: IndicatorValues,
        direction: str,
        df: pd.DataFrame,
        volume_spike_active: bool,
        macd_crossover_recent: bool,
    ) -> ScoreBreakdown:
        """Calculate aggressive score."""
        breakdown = ScoreBreakdown()

        # === CORE ===

        # 1. EMA alignment (0-15)
        breakdown.ema_alignment = self._score_ema(ind, direction)

        # 2. ADX (0-20) - lower thresholds
        breakdown.adx_strength = self._score_adx_aggressive(ind)

        # 3. RSI (0-12) - wide acceptance
        breakdown.rsi = self._score_rsi_wide(ind, direction)

        # 4. Volume (0-18)
        breakdown.volume = self._score_volume(ind)

        # 5. Price position (0-10)
        breakdown.price_position = self._score_price_position(ind, direction)

        # 6. DI spread (0-5)
        breakdown.di_spread = self._score_di(ind, direction)

        # === BONUS ===

        # 7. MACD crossover (+0-15) - HEAVY bonus
        if ind.macd is not None:
            breakdown.macd_bonus = self._score_macd_aggressive(
                ind, direction, macd_crossover_recent
            )

        # 8. Volume spike (+0-10)
        if volume_spike_active:
            breakdown.bb_bonus = 10.0
        elif ind.volume_ratio >= 2.5:
            breakdown.bb_bonus = 6.0
        elif ind.volume_ratio >= 2.0:
            breakdown.bb_bonus = 3.0

        # 9. Stochastic (+0-10)
        if ind.stoch_k is not None:
            breakdown.stoch_bonus = self._score_stoch_aggressive(ind, direction)

        return breakdown

    def _score_ema(self, ind: IndicatorValues, direction: str) -> float:
        """EMA alignment (0-15). Standard but lower cap."""
        ema9, ema21, ema50 = ind.ema9, ind.ema21, ind.ema50

        if ema21 == 0:
            return 0.0

        if direction == "LONG":
            if not (ema9 > ema21 > ema50):
                # Partial alignment: at least ema9 > ema21
                if ema9 > ema21:
                    return 5.0  # Aggressive accepts partial
                return 0.0
        else:
            if not (ema9 < ema21 < ema50):
                if ema9 < ema21:
                    return 5.0
                return 0.0

        gap = abs(ema9 - ema21) / ema21 * 100

        if gap > 0.8:
            return 15.0
        elif gap > 0.5:
            return 13.0
        elif gap > 0.3:
            return 10.0
        elif gap > 0.15:
            return 7.0
        else:
            return 5.0

    def _score_adx_aggressive(self, ind: IndicatorValues) -> float:
        """ADX score with LOWER thresholds (0-20)."""
        adx = ind.adx

        if adx >= 50:
            return 20.0
        elif adx >= 40:
            return 18.0
        elif adx >= 32:
            return 15.0
        elif adx >= 25:
            return 12.0
        elif adx >= 20:
            return 9.0
        elif adx >= 16:
            return 6.0
        elif adx >= 12:
            return 3.0
        else:
            return 0.0

    def _score_rsi_wide(self, ind: IndicatorValues, direction: str) -> float:
        """RSI with wider acceptance (0-12)."""
        rsi = ind.rsi

        if direction == "LONG":
            if 45 <= rsi <= 60:
                return 12.0
            elif 40 <= rsi <= 68:
                return 10.0
            elif 35 <= rsi <= 75:
                return 7.0
            elif 30 <= rsi <= 78:
                return 4.0
            elif rsi > 82:  # Only reject extreme overbought
                return 0.0
            else:
                return 2.0
        else:
            if 40 <= rsi <= 55:
                return 12.0
            elif 32 <= rsi <= 60:
                return 10.0
            elif 25 <= rsi <= 65:
                return 7.0
            elif 22 <= rsi <= 70:
                return 4.0
            elif rsi < 18:
                return 0.0
            else:
                return 2.0

    def _score_volume(self, ind: IndicatorValues) -> float:
        """Volume (0-18). Accepts lower volume."""
        ratio = ind.volume_ratio

        if ratio >= 3.0:
            return 18.0
        elif ratio >= 2.0:
            return 15.0
        elif ratio >= 1.5:
            return 12.0
        elif ratio >= 1.2:
            return 9.0
        elif ratio >= 1.0:
            return 7.0
        elif ratio >= 0.8:
            return 4.0  # Aggressive accepts below-average
        elif ratio >= 0.6:
            return 2.0
        else:
            return 0.0

    def _score_price_position(self, ind: IndicatorValues, direction: str) -> float:
        """Price position (0-10)."""
        price = ind.close
        ema9, ema21, ema50 = ind.ema9, ind.ema21, ind.ema50

        if direction == "LONG":
            if price > ema9 > ema21 > ema50:
                return 10.0
            elif price > ema9 > ema21:
                return 7.0
            elif price > ema9:
                return 5.0  # Aggressive: even partial is OK
            elif price > ema21:
                return 3.0
            else:
                return 0.0
        else:
            if price < ema9 < ema21 < ema50:
                return 10.0
            elif price < ema9 < ema21:
                return 7.0
            elif price < ema9:
                return 5.0
            elif price < ema21:
                return 3.0
            else:
                return 0.0

    def _score_di(self, ind: IndicatorValues, direction: str) -> float:
        """DI spread (0-5). Lower bar than conservative."""
        if direction == "LONG":
            spread = ind.di_plus - ind.di_minus
        else:
            spread = ind.di_minus - ind.di_plus

        if spread > 20:
            return 5.0
        elif spread > 12:
            return 4.0
        elif spread > 6:
            return 3.0
        elif spread > 0:
            return 2.0
        else:
            return 0.0

    def _score_macd_aggressive(
        self,
        ind: IndicatorValues,
        direction: str,
        recent_crossover: bool,
    ) -> float:
        """MACD with heavy crossover bonus (+0-15)."""
        if ind.macd is None or ind.macd_signal is None:
            return 0.0

        histogram = ind.macd_histogram or (ind.macd - ind.macd_signal)
        base = 0.0

        if direction == "LONG":
            if ind.macd > ind.macd_signal and histogram > 0:
                base = 8.0
            elif ind.macd > ind.macd_signal:
                base = 4.0
        else:
            if ind.macd < ind.macd_signal and histogram < 0:
                base = 8.0
            elif ind.macd < ind.macd_signal:
                base = 4.0

        # Recent crossover HEAVY bonus
        if recent_crossover:
            base = min(15.0, base + 7.0)

        return base

    def _score_stoch_aggressive(self, ind: IndicatorValues, direction: str) -> float:
        """Stochastic (+0-10). Wider acceptance."""
        if ind.stoch_k is None or ind.stoch_d is None:
            return 0.0

        k, d = ind.stoch_k, ind.stoch_d

        if direction == "LONG":
            if k > d and k < 30:
                return 10.0
            elif k > d and k < 50:
                return 7.0
            elif k > d and k < 70:
                return 4.0
            elif k > d:
                return 2.0
            return 0.0
        else:
            if k < d and k > 70:
                return 10.0
            elif k < d and k > 50:
                return 7.0
            elif k < d and k > 30:
                return 4.0
            elif k < d:
                return 2.0
            return 0.0


# =============================================================================
# MAIN STRATEGY CLASS
# =============================================================================


class SignalBoltAggressive(Strategy):
    """
    High-frequency aggressive strategy.

    Trades more often with lower entry requirements.
    Compensates with position sizing and trailing stops.
    Features unique MACD crossover override and volume spike bypass.
    """

    def __init__(self, config: Config):
        """Initialize aggressive strategy."""
        super().__init__(config)

        self.regime_detector = RegimeDetector()
        self.risk_manager = RiskManager(config)
        self.scorer = AggressiveScorer()

        # ----- Lower thresholds -----
        self.min_adx = config.get("strategy", "aggressive_min_adx", default=18.0)
        self.strong_adx = config.get("strategy", "aggressive_strong_adx", default=35.0)

        # Wide RSI window
        self.rsi_min = config.get("strategy", "aggressive_rsi_min", default=35.0)
        self.rsi_max = config.get("strategy", "aggressive_rsi_max", default=78.0)

        # Low volume requirement
        self.volume_multiplier = config.get(
            "strategy", "aggressive_volume_mult", default=0.8
        )
        self.volume_spike = config.get(
            "strategy", "aggressive_volume_spike", default=3.0
        )

        # ----- Unique features -----

        # MACD crossover override
        self.macd_override_enabled = config.get(
            "strategy", "aggressive_macd_override", default=True
        )
        self.macd_crossover_lookback = config.get(
            "strategy", "aggressive_macd_cross_lookback", default=3
        )
        self.macd_override_min_volume = config.get(
            "strategy", "aggressive_macd_override_min_vol", default=1.5
        )

        # Volume spike bypass
        self.volume_spike_bypass = config.get(
            "strategy", "aggressive_volume_spike_bypass", default=True
        )
        self.volume_spike_threshold = config.get(
            "strategy", "aggressive_volume_spike_threshold", default=3.0
        )
        self.volume_spike_adx_reduction = config.get(
            "strategy", "aggressive_spike_adx_reduction", default=5.0
        )
        self.volume_spike_rsi_widen = config.get(
            "strategy", "aggressive_spike_rsi_widen", default=5.0
        )
        self.volume_spike_score_reduction = config.get(
            "strategy", "aggressive_spike_score_reduction", default=10.0
        )

        # ----- Exit parameters -----
        self.sl_pct = config.get("strategy", "aggressive_sl_pct", default=2.0)
        self.timeout_minutes = config.get("strategy", "aggressive_timeout", default=120)
        self.trail_activation = config.get(
            "strategy", "aggressive_trail_activation", default=0.3
        )
        self.trail_distance = config.get(
            "strategy", "aggressive_trail_distance", default=0.4
        )
        self.be_activation = config.get(
            "strategy", "aggressive_be_activation", default=0.4
        )
        self.be_offset = config.get("strategy", "aggressive_be_offset", default=0.05)

        # ----- TP -----
        self.tp_enabled = config.get("strategy", "aggressive_tp_enabled", default=False)
        self.tp_pct = config.get("strategy", "aggressive_tp_pct", default=3.0)

        # ----- Kelly Criterion -----
        self.use_kelly = config.get("strategy", "aggressive_use_kelly", default=False)
        self.kelly_fraction = config.get(
            "strategy", "aggressive_kelly_fraction", default=0.25
        )

        # ----- Timeframe -----
        self.timeframe = config.get("strategy", "timeframe", default="5m")
        self.mtf_enabled = config.get("strategy", "mtf_enabled", default=False)

        # ----- ATR -----
        self.atr_period = config.get("strategy", "atr_period", default=14)
        self.use_atr_sl = config.get("strategy", "aggressive_use_atr_sl", default=False)
        self.atr_sl_multiplier = config.get(
            "strategy", "aggressive_atr_sl_mult", default=2.0
        )

        # Initialize indicator calculator
        self.indicator_calculator = IndicatorCalculator(
            enable_macd=True,
            enable_bb=True,
            enable_stoch=True,
            cache_enabled=True,
        )

        log.info(
            f"SignalBolt Aggressive initialized "
            f"(tf={self.timeframe}, min_adx={self.min_adx}, "
            f"rsi={self.rsi_min}-{self.rsi_max}, "
            f"vol>={self.volume_multiplier}x, "
            f"macd_override={'ON' if self.macd_override_enabled else 'OFF'}, "
            f"vol_spike_bypass={'ON' if self.volume_spike_bypass else 'OFF'}, "
            f"sl={self.sl_pct}%, timeout={self.timeout_minutes}min, "
            f"kelly={'ON' if self.use_kelly else 'OFF'})"
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
        Generate aggressive signal.

        Pipeline:
            1. Get indicators
            2. Detect regime
            3. Check for volume spike bypass (modifies thresholds)
            4. Check EMA alignment
            5. Check ADX (LOW threshold, with bypass)
            6. Check RSI (WIDE window, with bypass)
            7. Check volume (LOW requirement)
            8. Check DI alignment
            9. Check for MACD crossover override path
            10. Calculate aggressive score
            11. Validate (with possible spike score reduction)
            12. Build signal
        """
        if len(df) < 80:
            log.debug(f"{symbol}: Insufficient data ({len(df)} candles)")
            return None

        # 1. Get indicators
        indicators = self.indicator_calculator.get_latest(df)

        if current_price is None:
            current_price = df["close"].iloc[-1]

        # 2. Detect regime
        regime = self.regime_detector.detect(df)

        # 3. Check volume spike bypass
        volume_spike_active = (
            self.volume_spike_bypass
            and indicators.volume_ratio >= self.volume_spike_threshold
        )

        # Dynamic thresholds (modified by volume spike)
        effective_min_adx = self.min_adx
        effective_rsi_min = self.rsi_min
        effective_rsi_max = self.rsi_max
        score_reduction = 0.0

        if volume_spike_active:
            effective_min_adx -= self.volume_spike_adx_reduction
            effective_rsi_min -= self.volume_spike_rsi_widen
            effective_rsi_max += self.volume_spike_rsi_widen
            score_reduction = self.volume_spike_score_reduction
            log.debug(
                f"{symbol}: 🔥 Volume spike bypass ACTIVE "
                f"(vol={indicators.volume_ratio:.1f}x) - "
                f"ADX threshold: {effective_min_adx}, "
                f"RSI range: {effective_rsi_min}-{effective_rsi_max}"
            )

        # 4. Check EMA alignment
        direction = self._check_ema_alignment(indicators)

        # Check for MACD crossover override (alternative path)
        macd_crossover_recent = self._check_macd_crossover_recent(df)
        using_macd_override = False

        if direction is None:
            # Try MACD override path
            if self._can_use_macd_override(indicators, df):
                direction = self._get_macd_direction(indicators)
                using_macd_override = True
                log.debug(f"{symbol}: Using MACD crossover override path")
            else:
                log.debug(f"{symbol}: No EMA alignment and no MACD override")
                return None

        # 5. Check ADX (with bypass)
        if not using_macd_override:
            if indicators.adx < effective_min_adx:
                log.debug(
                    f"{symbol}: ADX too low "
                    f"({indicators.adx:.1f} < {effective_min_adx})"
                )
                return None

        # 6. Check RSI (with bypass)
        if not self._check_rsi_wide(
            indicators, direction, effective_rsi_min, effective_rsi_max
        ):
            log.debug(f"{symbol}: RSI outside window ({indicators.rsi:.1f})")
            return None

        # 7. Check volume (low requirement)
        if indicators.volume_ratio < self.volume_multiplier:
            log.debug(
                f"{symbol}: Volume below minimum "
                f"({indicators.volume_ratio:.2f} < {self.volume_multiplier})"
            )
            return None

        # 8. Check DI alignment (relaxed - only check if not using MACD override)
        if not using_macd_override:
            if not self._check_di_relaxed(indicators, direction):
                log.debug(f"{symbol}: DI misaligned")
                return None

        # 9. Calculate score
        score_breakdown = self.scorer.score(
            ind=indicators,
            direction=direction,
            df=df,
            volume_spike_active=volume_spike_active,
            macd_crossover_recent=macd_crossover_recent,
        )

        # 10. Get min_score (with volume spike reduction)
        preset = self._get_aggressive_preset(regime)
        min_score = preset.min_signal_score - score_reduction

        if score_breakdown.total < min_score:
            log.debug(
                f"{symbol}: Score too low ({score_breakdown.total:.1f} < {min_score})"
            )
            return None

        # 11. Build signal
        notes = self._build_aggressive_notes(
            indicators,
            regime,
            df,
            volume_spike_active,
            using_macd_override,
            macd_crossover_recent,
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
            f"🔥 AGGRESSIVE SIGNAL: {symbol} {direction} "
            f"score={score_breakdown.total:.1f} "
            f"regime={regime.value} price={current_price}"
            f"{' [MACD-OVERRIDE]' if using_macd_override else ''}"
            f"{' [VOL-SPIKE]' if volume_spike_active else ''}"
        )

        return signal

    # =========================================================================
    # CORE: ENTRY PLAN
    # =========================================================================

    def calculate_entry(
        self, signal: Signal, ticker: Ticker, wallet_balance: float
    ) -> EntryPlan:
        """
        Calculate aggressive entry plan.

        Differences from Original:
        - Always MARKET order (speed)
        - Larger position sizes
        - Optional Kelly Criterion sizing
        - Wider SL
        """
        entry_price = ticker.last_price

        regime = MarketRegime(signal.regime)
        preset = self._get_aggressive_preset(regime)

        # Stop-loss
        sl_price = self._calculate_aggressive_sl(
            entry_price=entry_price,
            direction=signal.direction,
            indicators=signal.indicators,
            preset=preset,
        )

        # Position sizing
        position_size = self.risk_manager.calculate_position_size(
            balance=wallet_balance,
            entry_price=entry_price,
            stop_loss_price=sl_price,
            direction=signal.direction,
        )

        # Apply regime wallet_pct
        max_size_usd = wallet_balance * (preset.wallet_pct / 100)
        actual_size_usd = min(position_size.size_usd, max_size_usd)

        # Kelly Criterion boost (optional)
        if self.use_kelly:
            # Simple Kelly: f* = (bp - q) / b
            # Assuming 52% win rate, 1.5:1 reward/risk
            kelly_pct = self._calculate_kelly(0.52, 1.5) * self.kelly_fraction
            kelly_size = wallet_balance * kelly_pct
            actual_size_usd = max(actual_size_usd, kelly_size)
            actual_size_usd = min(actual_size_usd, max_size_usd)  # Cap at max

        quantity = actual_size_usd / entry_price

        if signal.direction == "LONG":
            sl_pct = ((entry_price - sl_price) / entry_price) * 100
        else:
            sl_pct = ((sl_price - entry_price) / entry_price) * 100

        notes = (
            f"AGGRESSIVE | Risk: ${position_size.risk_usd:.2f} "
            f"({position_size.risk_pct:.2f}%), "
            f"SL: {sl_pct:.2f}%, "
            f"Regime: {regime.value}, "
            f"Size: ${actual_size_usd:.2f}"
        )

        if self.use_kelly:
            notes += " [Kelly]"

        plan = EntryPlan(
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=entry_price,
            position_size_usd=actual_size_usd,
            quantity=quantity,
            stop_loss_price=sl_price,
            stop_loss_pct=sl_pct,
            use_limit=False,
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
        Calculate aggressive exit plan.

        Aggressive exits LET WINNERS RUN:
        - Wider SL (2.0%)
        - Earlier trailing activation (+0.3%)
        - Medium trailing distance (0.4%)
        - Longer timeout (120 min)
        - Break-even at +0.4%
        - Optional fixed TP at 3.0%
        """
        regime = MarketRegime.RANGE
        preset = self._get_aggressive_preset(regime)

        # Stop-loss
        sl_price = self._calculate_aggressive_sl(
            entry_price=entry_price,
            direction=direction,
            indicators=indicators,
            preset=preset,
        )

        if direction == "LONG":
            sl_pct = ((entry_price - sl_price) / entry_price) * 100
        else:
            sl_pct = ((sl_price - entry_price) / entry_price) * 100

        # Take-profit
        tp_price = None
        tp_pct = None

        if self.tp_enabled:
            tp_pct = self.tp_pct
            if direction == "LONG":
                tp_price = entry_price * (1 + tp_pct / 100)
            else:
                tp_price = entry_price * (1 - tp_pct / 100)

        # Trailing
        trail_dist = self.trail_distance

        if self.use_atr_sl and indicators and indicators.atr > 0:
            atr_trail = (indicators.atr * 1.0 / entry_price) * 100
            trail_dist = min(trail_dist, max(0.2, atr_trail))

        plan = ExitPlan(
            stop_loss_price=sl_price,
            stop_loss_pct=sl_pct,
            take_profit_price=tp_price,
            take_profit_pct=tp_pct,
            trailing_active=True,
            trailing_activation_pct=self.trail_activation,
            trailing_distance_pct=trail_dist,
            breakeven_enabled=True,
            breakeven_activation_pct=self.be_activation,
            breakeven_offset_pct=self.be_offset,
            timeout_minutes=self.timeout_minutes,
            min_profit_on_timeout_pct=0.10,
        )

        return plan

    # =========================================================================
    # UNIQUE FEATURE: MACD CROSSOVER OVERRIDE
    # =========================================================================

    def _can_use_macd_override(
        self, indicators: IndicatorValues, df: pd.DataFrame
    ) -> bool:
        """
        Check if MACD crossover override conditions are met.

        Override allows entry even without full EMA alignment if:
        1. MACD override is enabled in config
        2. MACD histogram JUST turned positive (recent crossover)
        3. Volume > override minimum (1.5x)
        4. At least partial EMA alignment (EMA9 > EMA21)
        """
        if not self.macd_override_enabled:
            return False

        if indicators.macd is None or indicators.macd_signal is None:
            return False

        # Check volume requirement
        if indicators.volume_ratio < self.macd_override_min_volume:
            return False

        # Check at least partial EMA alignment
        if not (indicators.ema9 > indicators.ema21):
            return False

        # Check recent MACD crossover
        return self._check_macd_crossover_recent(df)

    def _check_macd_crossover_recent(self, df: pd.DataFrame) -> bool:
        """
        Check if MACD histogram crossed from negative to positive recently.

        Looks back N bars for a sign change in MACD histogram.
        """
        if "macd_histogram" not in df.columns:
            return False

        lookback = min(self.macd_crossover_lookback, len(df) - 1)
        if lookback < 1:
            return False

        recent = df["macd_histogram"].iloc[-(lookback + 1) :]

        if recent.isna().any():
            return False

        values = recent.values

        # Check for sign change (negative -> positive)
        for i in range(1, len(values)):
            if values[i - 1] <= 0 and values[i] > 0:
                return True
            # Also check positive -> negative for SHORT (future)

        return False

    def _get_macd_direction(self, indicators: IndicatorValues) -> str:
        """Get direction from MACD."""
        if indicators.macd is not None and indicators.macd_signal is not None:
            if indicators.macd > indicators.macd_signal:
                return "LONG"
            else:
                return "SHORT"
        return "LONG"  # Default

    # =========================================================================
    # HELPER: SIGNAL CHECKS
    # =========================================================================

    def _check_ema_alignment(self, indicators: IndicatorValues) -> Optional[str]:
        """Standard EMA alignment."""
        if indicators.ema9 > indicators.ema21 > indicators.ema50:
            return "LONG"
        return None

    def _check_rsi_wide(
        self,
        indicators: IndicatorValues,
        direction: str,
        rsi_min: float,
        rsi_max: float,
    ) -> bool:
        """RSI check with dynamic (potentially widened) thresholds."""
        if direction == "LONG":
            return rsi_min <= indicators.rsi <= rsi_max
        else:
            return (100 - rsi_max) <= indicators.rsi <= (100 - rsi_min)

    def _check_di_relaxed(self, indicators: IndicatorValues, direction: str) -> bool:
        """Relaxed DI check (just direction, no spread requirement)."""
        if direction == "LONG":
            return indicators.di_plus > indicators.di_minus
        else:
            return indicators.di_minus > indicators.di_plus

    # =========================================================================
    # HELPER: STOP LOSS & KELLY
    # =========================================================================

    def _calculate_aggressive_sl(
        self,
        entry_price: float,
        direction: str,
        indicators: Optional[IndicatorValues],
        preset: RegimePreset,
    ) -> float:
        """
        Calculate aggressive SL.

        Uses wider stops to give trades room to breathe.
        """
        if self.use_atr_sl and indicators and indicators.atr > 0:
            if direction == "LONG":
                sl_price = entry_price - (indicators.atr * self.atr_sl_multiplier)
            else:
                sl_price = entry_price + (indicators.atr * self.atr_sl_multiplier)
        else:
            if direction == "LONG":
                sl_price = entry_price * (1 - preset.stop_loss_pct / 100)
            else:
                sl_price = entry_price * (1 + preset.stop_loss_pct / 100)

        return sl_price

    def _calculate_kelly(self, win_rate: float, reward_risk: float) -> float:
        """
        Calculate Kelly Criterion fraction.

        f* = (bp - q) / b
        where:
            b = reward/risk ratio
            p = win rate
            q = 1 - p (loss rate)
        """
        p = win_rate
        q = 1.0 - p
        b = reward_risk

        kelly = (b * p - q) / b

        # Clamp between 0 and 1
        return max(0.0, min(1.0, kelly))

    # =========================================================================
    # HELPER: REGIME PRESET
    # =========================================================================

    def _get_aggressive_preset(self, regime: MarketRegime) -> RegimePreset:
        """Get aggressive-specific regime preset."""
        if regime in AGGRESSIVE_REGIME_PRESETS:
            return AGGRESSIVE_REGIME_PRESETS[regime]
        return get_regime_preset(regime, self.config)

    # =========================================================================
    # HELPER: SIGNAL NOTES
    # =========================================================================

    def _build_aggressive_notes(
        self,
        indicators: IndicatorValues,
        regime: MarketRegime,
        df: pd.DataFrame,
        volume_spike_active: bool,
        using_macd_override: bool,
        macd_crossover_recent: bool,
    ) -> str:
        """Build detailed aggressive signal notes."""
        notes = []

        notes.append("🔥AGGRESSIVE")

        # Entry path
        if using_macd_override:
            notes.append("⚡MACD-OVERRIDE")
        else:
            notes.append("EMA✓")

        # Volume spike bypass
        if volume_spike_active:
            notes.append(f"🌊VOL-SPIKE({indicators.volume_ratio:.1f}x)")

        # ADX
        adx_str = f"ADX={indicators.adx:.1f}"
        if indicators.adx > self.strong_adx:
            adx_str = f"💪{adx_str}"
        notes.append(adx_str)

        # RSI
        notes.append(f"RSI={indicators.rsi:.1f}")

        # Volume
        vol_str = f"Vol={indicators.volume_ratio:.1f}x"
        if indicators.volume_ratio >= self.volume_spike:
            vol_str = f"🔥{vol_str}"
        notes.append(vol_str)

        # MACD
        if indicators.macd_histogram is not None:
            macd_dir = "↑" if indicators.macd_histogram > 0 else "↓"
            macd_str = f"MACD{macd_dir}"
            if macd_crossover_recent:
                macd_str += "⚡CROSS"
            notes.append(macd_str)

        # Stochastic
        if indicators.stoch_k is not None:
            k_d = "K>D" if indicators.stoch_k > indicators.stoch_d else "K<D"
            notes.append(f"Stoch({k_d})")

        # BB position
        if indicators.bb_upper is not None and indicators.bb_lower is not None:
            bb_range = indicators.bb_upper - indicators.bb_lower
            if bb_range > 0:
                pct_pos = (indicators.close - indicators.bb_lower) / bb_range
                notes.append(f"BB={pct_pos:.0%}")

        # Regime
        notes.append(f"Regime:{regime.value}")

        # Kelly
        if self.use_kelly:
            notes.append("📐Kelly")

        return ", ".join(notes)

    # =========================================================================
    # METADATA
    # =========================================================================

    def get_min_data_length(self) -> int:
        """Minimum candles needed."""
        return 80

    def supports_timeframe(self, timeframe: str) -> bool:
        """Aggressive works on all timeframes."""
        return True

    def get_optimal_timeframes(self) -> list[str]:
        """Optimal timeframes for aggressive strategy."""
        return ["3m", "5m", "15m", "1h"]

    def __repr__(self) -> str:
        return (
            f"SignalBoltAggressive("
            f"tf={self.timeframe}, "
            f"adx>={self.min_adx}, "
            f"rsi={self.rsi_min}-{self.rsi_max}, "
            f"vol>={self.volume_multiplier}x, "
            f"macd_override={'ON' if self.macd_override_enabled else 'OFF'}, "
            f"vol_bypass={'ON' if self.volume_spike_bypass else 'OFF'}, "
            f"sl={self.sl_pct}%, "
            f"timeout={self.timeout_minutes}min, "
            f"kelly={'ON' if self.use_kelly else 'OFF'})"
        )
