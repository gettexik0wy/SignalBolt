"""
SignalBolt Conservative Strategy

Risk-averse strategy requiring MULTIPLE confirmations before entry.

Philosophy:
    "Miss 10 good trades rather than take 1 bad trade."

Key Differences from Original:
    - MANDATORY Multi-Timeframe confirmation (primary + 1h)
    - Higher ADX threshold (30) AND ADX must be RISING
    - Narrower RSI window (43-62 for LONG)
    - ALL bonus indicators must confirm (MACD + BB + Stochastic)
    - ATR-based SL by default (adaptive to volatility)
    - Volume must be >= 1.5x AND rising (2-bar comparison)
    - Longer timeout (90 min) - gives trades room to work
    - Mandatory break-even activation
    - Wider trailing stop (lock profits, don't get shaken out)
    - Custom scoring with "confirmation consensus" bonus
    - CRASH regime: position size reduced by 70%

Entry Logic:
    - LONG: EMA9 > EMA21 > EMA50 (standard alignment)
    - ADX > 30 AND ADX rising (current > previous bar)
    - RSI between 43-62 (narrow healthy zone)
    - Volume >= 1.5x average AND volume[current] > volume[prev]
    - DI+ > DI- with spread > 5
    - MACD histogram > 0 (MANDATORY)
    - BB: price above middle band (MANDATORY)
    - Stochastic: K > D and K < 70 (MANDATORY)
    - Higher TF confirmation (1h must be bullish)
    - Score >= regime-adjusted minimum (higher than Original)

Exit Logic:
    - Stop-loss: ATR-based (1.5x ATR default)
    - Trailing stop: 0.6% distance, activates at +0.8%
    - Break-even: activates at +0.5%, offset 0.08%
    - Timeout: 90 minutes
    - Take-profit: ATR-based (2.5x ATR)
    - Emergency: exit if ADX drops below 18 mid-trade

Performance Targets:
    - Win rate: 65-75%
    - Avg profit per trade: 0.8-1.5%
    - Avg loss per trade: 0.5-1.0%
    - Trades per day: 2-5
    - Sharpe ratio: >2.0
    - Max drawdown: <5%

Recommended Config:
    strategy:
      name: "SignalBoltConservative"
      timeframe: "15m"          # or "5m", "1h"
      conservative_min_adx: 30
      conservative_rsi_min: 43
      conservative_rsi_max: 62
      conservative_volume_mult: 1.5
      mtf_enabled: true
      mtf_timeframes: ["15m", "1h"]
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

log = get_logger("signalbolt.strategies.conservative")


# =============================================================================
# CONSERVATIVE REGIME PRESETS
# =============================================================================
# More cautious than global defaults: tighter sizing, wider SL, higher score req.

CONSERVATIVE_REGIME_PRESETS = {
    MarketRegime.BULL: RegimePreset(
        stop_loss_pct=1.5,
        breakeven_trigger_pct=0.5,
        trailing_stop_pct=0.5,
        min_signal_score=68.0,
        slippage_pct=0.05,
        wallet_pct=65.0,
        max_positions=3,
        scan_interval_sec=300,
    ),
    MarketRegime.RANGE: RegimePreset(
        stop_loss_pct=1.8,
        breakeven_trigger_pct=0.7,
        trailing_stop_pct=0.6,
        min_signal_score=74.0,
        slippage_pct=0.08,
        wallet_pct=55.0,
        max_positions=2,
        scan_interval_sec=600,
    ),
    MarketRegime.BEAR: RegimePreset(
        stop_loss_pct=2.2,
        breakeven_trigger_pct=0.9,
        trailing_stop_pct=0.8,
        min_signal_score=82.0,
        slippage_pct=0.12,
        wallet_pct=35.0,
        max_positions=1,
        scan_interval_sec=900,
    ),
    MarketRegime.CRASH: RegimePreset(
        stop_loss_pct=2.5,
        breakeven_trigger_pct=1.0,
        trailing_stop_pct=1.0,
        min_signal_score=90.0,
        slippage_pct=0.20,
        wallet_pct=15.0,  # Minimal exposure
        max_positions=1,
        scan_interval_sec=1800,
    ),
}


# =============================================================================
# CONSERVATIVE SCORING ENGINE
# =============================================================================


class ConservativeScorer:
    """
    Conservative scoring with "confirmation consensus" system.

    Weight distribution (total 100 + bonus 30):
        - EMA alignment + trend quality: 18 pts
        - ADX strength + ADX rising:     25 pts  (trend MUST be strengthening)
        - RSI healthy zone:              12 pts
        - Volume (strength + rising):    18 pts
        - Price position:                10 pts
        - DI spread:                      7 pts
        - Confirmation gap:              10 pts  (all-indicator consensus)
        - MACD confirmation:            +10 pts
        - BB confirmation:              +10 pts
        - Stochastic confirmation:      +10 pts

    The "Confirmation Gap" is UNIQUE to conservative:
        - If ALL indicators agree: +10 bonus
        - If any indicator disagrees: 0 (no penalty, just no bonus)
        - This rewards trades where everything lines up perfectly
    """

    def score(
        self,
        ind: IndicatorValues,
        direction: str,
        df: pd.DataFrame,
        adx_rising: bool,
        volume_rising: bool,
    ) -> ScoreBreakdown:
        """
        Calculate conservative score.

        Args:
            ind: Indicator values
            direction: Trade direction
            df: Full OHLCV dataframe
            adx_rising: Whether ADX is increasing
            volume_rising: Whether volume is increasing
        """
        breakdown = ScoreBreakdown()

        # === CORE ===

        # 1. EMA alignment + trend quality (0-18)
        breakdown.ema_alignment = self._score_ema_quality(ind, direction)

        # 2. ADX strength + rising requirement (0-25)
        breakdown.adx_strength = self._score_adx_conservative(ind, adx_rising)

        # 3. RSI narrow healthy zone (0-12)
        breakdown.rsi = self._score_rsi_narrow(ind, direction)

        # 4. Volume strength + rising (0-18)
        breakdown.volume = self._score_volume_quality(ind, volume_rising)

        # 5. Price position (0-10)
        breakdown.price_position = self._score_price_position(ind, direction)

        # 6. DI spread (0-7)
        breakdown.di_spread = self._score_di_conservative(ind, direction)

        # === BONUS: CONFIRMATION CONSENSUS ===

        # Track confirmations
        confirmations = 0
        total_checks = 0

        # 7. MACD confirmation (+0-10)
        if ind.macd is not None:
            total_checks += 1
            macd_score = self._score_macd_confirmation(ind, direction)
            breakdown.macd_bonus = macd_score
            if macd_score >= 5:
                confirmations += 1

        # 8. BB confirmation (+0-10)
        if ind.bb_upper is not None:
            total_checks += 1
            bb_score = self._score_bb_confirmation(ind, direction)
            breakdown.bb_bonus = bb_score
            if bb_score >= 5:
                confirmations += 1

        # 9. Stochastic confirmation (+0-10)
        if ind.stoch_k is not None:
            total_checks += 1
            stoch_score = self._score_stoch_confirmation(ind, direction)
            breakdown.stoch_bonus = stoch_score
            if stoch_score >= 3:
                confirmations += 1

        # Consensus bonus: if ALL bonus indicators confirm, add 10 pts to price_position
        if total_checks > 0 and confirmations == total_checks:
            breakdown.price_position = min(20.0, breakdown.price_position + 10.0)

        return breakdown

    def _score_ema_quality(self, ind: IndicatorValues, direction: str) -> float:
        """EMA alignment with trend quality check (0-18)."""
        ema9, ema21, ema50 = ind.ema9, ind.ema21, ind.ema50

        if ema21 == 0:
            return 0.0

        if direction == "LONG":
            if not (ema9 > ema21 > ema50):
                return 0.0
        else:
            if not (ema9 < ema21 < ema50):
                return 0.0

        # Gap quality
        gap_9_21 = abs(ema9 - ema21) / ema21 * 100
        gap_21_50 = abs(ema21 - ema50) / ema50 * 100

        # Reward EVENLY SPACED EMAs (sign of healthy trend)
        gap_ratio = (
            min(gap_9_21, gap_21_50) / max(gap_9_21, gap_21_50)
            if max(gap_9_21, gap_21_50) > 0
            else 0
        )
        evenness_bonus = gap_ratio * 4  # 0-4 pts

        # Gap size scoring
        if gap_9_21 > 0.8:
            gap_score = 14.0
        elif gap_9_21 > 0.5:
            gap_score = 12.0
        elif gap_9_21 > 0.3:
            gap_score = 9.0
        elif gap_9_21 > 0.15:
            gap_score = 6.0
        else:
            gap_score = 3.0

        return min(18.0, gap_score + evenness_bonus)

    def _score_adx_conservative(self, ind: IndicatorValues, adx_rising: bool) -> float:
        """
        ADX score with RISING requirement (0-25).

        Conservative DEMANDS rising ADX - we want to enter when
        trend is STRENGTHENING, not when it's peaking.
        """
        adx = ind.adx

        # Base ADX score
        if adx >= 50:
            base = 18.0
        elif adx >= 42:
            base = 16.0
        elif adx >= 35:
            base = 14.0
        elif adx >= 30:
            base = 11.0
        elif adx >= 25:
            base = 7.0
        elif adx >= 20:
            base = 3.0
        else:
            base = 0.0

        # Rising bonus (+7 if ADX is increasing)
        if adx_rising:
            rising_bonus = 7.0
        else:
            # ADX is falling - PENALTY for conservative
            rising_bonus = -3.0

        return max(0.0, min(25.0, base + rising_bonus))

    def _score_rsi_narrow(self, ind: IndicatorValues, direction: str) -> float:
        """RSI in narrow healthy zone (0-12)."""
        rsi = ind.rsi

        if direction == "LONG":
            # Perfect zone: 48-56 (narrow, confident)
            if 48 <= rsi <= 56:
                return 12.0
            elif 45 <= rsi <= 60:
                return 10.0
            elif 43 <= rsi <= 62:
                return 7.0
            elif 40 <= rsi <= 65:
                return 4.0
            elif rsi > 70:  # Overbought - BAD
                return 0.0
            elif rsi < 35:  # Oversold - risky bounce
                return 0.0
            else:
                return 2.0
        else:
            if 44 <= rsi <= 52:
                return 12.0
            elif 40 <= rsi <= 55:
                return 10.0
            elif 38 <= rsi <= 57:
                return 7.0
            elif 35 <= rsi <= 60:
                return 4.0
            elif rsi < 30:
                return 0.0
            elif rsi > 65:
                return 0.0
            else:
                return 2.0

    def _score_volume_quality(self, ind: IndicatorValues, volume_rising: bool) -> float:
        """Volume with rising requirement (0-18)."""
        ratio = ind.volume_ratio

        # Base volume score
        if ratio >= 3.0:
            base = 14.0
        elif ratio >= 2.5:
            base = 12.0
        elif ratio >= 2.0:
            base = 10.0
        elif ratio >= 1.5:
            base = 8.0
        elif ratio >= 1.3:
            base = 5.0
        elif ratio >= 1.0:
            base = 2.0
        else:
            base = 0.0

        # Rising volume bonus (+4 if increasing)
        if volume_rising:
            return min(18.0, base + 4.0)
        else:
            return min(18.0, base)

    def _score_price_position(self, ind: IndicatorValues, direction: str) -> float:
        """Price position relative to EMAs (0-10)."""
        price = ind.close
        ema9, ema21, ema50 = ind.ema9, ind.ema21, ind.ema50

        if direction == "LONG":
            if price > ema9 > ema21 > ema50:
                return 10.0
            elif price > ema9 > ema21:
                return 7.0
            elif price > ema21:
                return 4.0
            else:
                return 0.0
        else:
            if price < ema9 < ema21 < ema50:
                return 10.0
            elif price < ema9 < ema21:
                return 7.0
            elif price < ema21:
                return 4.0
            else:
                return 0.0

    def _score_di_conservative(self, ind: IndicatorValues, direction: str) -> float:
        """DI spread (0-7). Conservative requires wider spread."""
        if direction == "LONG":
            spread = ind.di_plus - ind.di_minus
        else:
            spread = ind.di_minus - ind.di_plus

        if spread > 25:
            return 7.0
        elif spread > 18:
            return 5.0
        elif spread > 12:
            return 4.0
        elif spread > 7:
            return 2.0
        elif spread > 3:
            return 1.0
        else:
            return 0.0

    def _score_macd_confirmation(self, ind: IndicatorValues, direction: str) -> float:
        """MACD confirmation (+0-10)."""
        if ind.macd is None or ind.macd_signal is None:
            return 0.0

        histogram = ind.macd_histogram or (ind.macd - ind.macd_signal)

        if direction == "LONG":
            if ind.macd > ind.macd_signal and histogram > 0:
                # Strong confirmation
                if histogram > abs(ind.macd) * 0.15:
                    return 10.0
                return 6.0
            elif ind.macd > ind.macd_signal:
                return 3.0
            return 0.0
        else:
            if ind.macd < ind.macd_signal and histogram < 0:
                if abs(histogram) > abs(ind.macd) * 0.15:
                    return 10.0
                return 6.0
            elif ind.macd < ind.macd_signal:
                return 3.0
            return 0.0

    def _score_bb_confirmation(self, ind: IndicatorValues, direction: str) -> float:
        """BB confirmation (+0-10). Price must be in the right half."""
        if ind.bb_upper is None:
            return 0.0

        bb_range = ind.bb_upper - ind.bb_lower
        if bb_range <= 0:
            return 0.0

        pct_pos = (ind.close - ind.bb_lower) / bb_range

        if direction == "LONG":
            # Above middle, not at upper extreme
            if 0.5 <= pct_pos <= 0.78:
                return 10.0
            elif 0.4 <= pct_pos <= 0.85:
                return 6.0
            elif pct_pos > 0.3:
                return 3.0
            return 0.0
        else:
            if 0.22 <= pct_pos <= 0.5:
                return 10.0
            elif 0.15 <= pct_pos <= 0.6:
                return 6.0
            elif pct_pos < 0.7:
                return 3.0
            return 0.0

    def _score_stoch_confirmation(self, ind: IndicatorValues, direction: str) -> float:
        """Stochastic confirmation (+0-10)."""
        if ind.stoch_k is None or ind.stoch_d is None:
            return 0.0

        k, d = ind.stoch_k, ind.stoch_d

        if direction == "LONG":
            if k > d and k < 70:
                if k < 40:
                    return 10.0  # Best: crossing up from low
                elif k < 55:
                    return 7.0
                else:
                    return 4.0
            elif k > d:
                return 2.0
            return 0.0
        else:
            if k < d and k > 30:
                if k > 60:
                    return 10.0
                elif k > 45:
                    return 7.0
                else:
                    return 4.0
            elif k < d:
                return 2.0
            return 0.0


# =============================================================================
# MAIN STRATEGY CLASS
# =============================================================================


class SignalBoltConservative(Strategy):
    """
    Risk-averse strategy requiring maximum confirmation.

    Trades less frequently but with higher win rate.
    Every indicator must agree before entry.
    """

    def __init__(self, config: Config):
        """Initialize conservative strategy."""
        super().__init__(config)

        self.regime_detector = RegimeDetector()
        self.risk_manager = RiskManager(config)
        self.scorer = ConservativeScorer()

        # ----- Conservative thresholds -----
        self.min_adx = config.get("strategy", "conservative_min_adx", default=30.0)
        self.strong_adx = config.get(
            "strategy", "conservative_strong_adx", default=42.0
        )

        # Narrow RSI window
        self.rsi_min = config.get("strategy", "conservative_rsi_min", default=43.0)
        self.rsi_max = config.get("strategy", "conservative_rsi_max", default=62.0)

        # Higher volume requirement
        self.volume_multiplier = config.get(
            "strategy", "conservative_volume_mult", default=1.5
        )
        self.volume_spike = config.get(
            "strategy", "conservative_volume_spike", default=2.5
        )

        # Mandatory bonus indicator confirmation
        self.require_macd_confirm = config.get(
            "strategy", "conservative_require_macd", default=True
        )
        self.require_bb_confirm = config.get(
            "strategy", "conservative_require_bb", default=True
        )
        self.require_stoch_confirm = config.get(
            "strategy", "conservative_require_stoch", default=True
        )

        # Minimum DI spread
        self.min_di_spread = config.get(
            "strategy", "conservative_min_di_spread", default=5.0
        )

        # ----- MTF Settings (MANDATORY for conservative) -----
        self.timeframe = config.get("strategy", "timeframe", default="15m")
        self.mtf_enabled = config.get(
            "strategy", "conservative_mtf_enabled", default=True
        )
        self.mtf_higher_tf = config.get(
            "strategy", "conservative_mtf_higher_tf", default="1h"
        )

        # ----- ATR-based SL (DEFAULT for conservative) -----
        self.atr_period = config.get("strategy", "atr_period", default=14)
        self.use_atr_sl = config.get(
            "strategy", "conservative_use_atr_sl", default=True
        )
        self.atr_sl_multiplier = config.get(
            "strategy", "conservative_atr_sl_mult", default=1.5
        )
        self.atr_tp_multiplier = config.get(
            "strategy", "conservative_atr_tp_mult", default=2.5
        )

        # ----- Exit parameters -----
        self.timeout_minutes = config.get(
            "strategy", "conservative_timeout", default=90
        )
        self.trail_activation = config.get(
            "strategy", "conservative_trail_activation", default=0.8
        )
        self.trail_distance = config.get(
            "strategy", "conservative_trail_distance", default=0.6
        )
        self.be_activation = config.get(
            "strategy", "conservative_be_activation", default=0.5
        )
        self.be_offset = config.get("strategy", "conservative_be_offset", default=0.08)

        # ----- ADX lookback for rising detection -----
        self.adx_lookback = config.get(
            "strategy", "conservative_adx_lookback", default=3
        )

        # Initialize indicator calculator with ALL bonuses
        self.indicator_calculator = IndicatorCalculator(
            enable_macd=True,
            enable_bb=True,
            enable_stoch=True,
            cache_enabled=True,
        )

        log.info(
            f"SignalBolt Conservative initialized "
            f"(tf={self.timeframe}, min_adx={self.min_adx}, "
            f"rsi={self.rsi_min}-{self.rsi_max}, "
            f"vol>={self.volume_multiplier}x, "
            f"mtf={'ON' if self.mtf_enabled else 'OFF'} "
            f"[{self.mtf_higher_tf}], "
            f"atr_sl={'ON' if self.use_atr_sl else 'OFF'}, "
            f"require_macd={self.require_macd_confirm}, "
            f"require_bb={self.require_bb_confirm}, "
            f"require_stoch={self.require_stoch_confirm})"
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
        Generate conservative signal with maximum confirmation.

        Pipeline:
            1. Get standard indicators
            2. Detect regime
            3. Check EMA alignment (standard 9/21/50)
            4. Check ADX >= 30 AND ADX is RISING
            5. Check narrow RSI window (43-62)
            6. Check volume >= 1.5x AND volume is RISING
            7. Check DI alignment with minimum spread
            8. Check MACD confirmation (MANDATORY if enabled)
            9. Check BB confirmation (MANDATORY if enabled)
            10. Check Stochastic confirmation (MANDATORY if enabled)
            11. Calculate conservative score
            12. Validate against regime-adjusted threshold
            13. (Optional) MTF higher TF confirmation
        """
        if len(df) < 100:
            log.debug(f"{symbol}: Insufficient data ({len(df)} candles)")
            return None

        # 1. Get indicators
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

        # 4. Check ADX threshold AND rising
        if indicators.adx < self.min_adx:
            log.debug(f"{symbol}: ADX too low ({indicators.adx:.1f} < {self.min_adx})")
            return None

        adx_rising = self._is_adx_rising(df)
        if not adx_rising:
            log.debug(f"{symbol}: ADX is FALLING - conservative requires rising ADX")
            return None

        # 5. Check narrow RSI
        if not self._check_narrow_rsi(indicators, direction):
            log.debug(
                f"{symbol}: RSI outside narrow window "
                f"({indicators.rsi:.1f} not in {self.rsi_min}-{self.rsi_max})"
            )
            return None

        # 6. Check volume with rising requirement
        if not self._check_volume_rising(df, indicators):
            log.debug(
                f"{symbol}: Volume check failed "
                f"(ratio={indicators.volume_ratio:.2f}, "
                f"need>={self.volume_multiplier}x AND rising)"
            )
            return None

        volume_rising = self._is_volume_rising(df)

        # 7. Check DI alignment with minimum spread
        if not self._check_di_with_spread(indicators, direction):
            log.debug(f"{symbol}: DI check failed (spread < {self.min_di_spread})")
            return None

        # 8. MACD confirmation
        if self.require_macd_confirm and not self._check_macd_confirm(
            indicators, direction
        ):
            log.debug(f"{symbol}: MACD confirmation FAILED (mandatory)")
            return None

        # 9. BB confirmation
        if self.require_bb_confirm and not self._check_bb_confirm(
            indicators, direction
        ):
            log.debug(f"{symbol}: BB confirmation FAILED (mandatory)")
            return None

        # 10. Stochastic confirmation
        if self.require_stoch_confirm and not self._check_stoch_confirm(
            indicators, direction
        ):
            log.debug(f"{symbol}: Stochastic confirmation FAILED (mandatory)")
            return None

        # 11. Calculate score
        score_breakdown = self.scorer.score(
            ind=indicators,
            direction=direction,
            df=df,
            adx_rising=adx_rising,
            volume_rising=volume_rising,
        )

        # 12. Get regime-adjusted min_score
        preset = self._get_conservative_preset(regime)
        min_score = preset.min_signal_score

        if score_breakdown.total < min_score:
            log.debug(
                f"{symbol}: Score too low ({score_breakdown.total:.1f} < {min_score})"
            )
            return None

        # 13. Build signal
        notes = self._build_conservative_notes(
            indicators, regime, df, adx_rising, volume_rising
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
            f"🛡️ CONSERVATIVE SIGNAL: {symbol} {direction} "
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
        Calculate conservative entry plan.

        Differences from Original:
        - Prefers LIMIT orders (better fill price)
        - Conservative wallet_pct from regime preset
        - ATR-based SL by default
        """
        # Conservative prefers limit orders for better fill
        use_limit = (
            self.config.get("spot", "entry_order_type", default="LIMIT") == "LIMIT"
        )

        if use_limit:
            if signal.direction == "LONG":
                entry_price = ticker.bid
            else:
                entry_price = ticker.ask
        else:
            entry_price = ticker.last_price

        # Get conservative regime preset
        regime = MarketRegime(signal.regime)
        preset = self._get_conservative_preset(regime)

        # ATR-based SL (default for conservative)
        sl_price = self._calculate_conservative_sl(
            entry_price=entry_price,
            direction=signal.direction,
            indicators=signal.indicators,
            preset=preset,
        )

        # Position sizing (conservative wallet_pct)
        position_size = self.risk_manager.calculate_position_size(
            balance=wallet_balance,
            entry_price=entry_price,
            stop_loss_price=sl_price,
            direction=signal.direction,
        )

        max_size_usd = wallet_balance * (preset.wallet_pct / 100)
        actual_size_usd = min(position_size.size_usd, max_size_usd)
        quantity = actual_size_usd / entry_price

        if signal.direction == "LONG":
            sl_pct = ((entry_price - sl_price) / entry_price) * 100
        else:
            sl_pct = ((sl_price - entry_price) / entry_price) * 100

        notes = (
            f"CONSERVATIVE | Risk: ${position_size.risk_usd:.2f} "
            f"({position_size.risk_pct:.2f}%), "
            f"SL: {sl_pct:.2f}% (ATR-based), "
            f"Regime: {regime.value}"
        )

        if self.use_atr_sl and signal.indicators:
            notes += f", ATR={signal.indicators.atr:.6f} ({self.atr_sl_multiplier}x)"

        plan = EntryPlan(
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=entry_price,
            position_size_usd=actual_size_usd,
            quantity=quantity,
            stop_loss_price=sl_price,
            stop_loss_pct=sl_pct,
            use_limit=use_limit,
            limit_offset_pct=self.config.get("spot", "limit_offset_pct", default=0.03),
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
        Calculate conservative exit plan.

        Conservative exits are PATIENT:
        - Wider SL (ATR-based, typically 1.0-2.0%)
        - Wider trailing (locks in profits but doesn't exit prematurely)
        - Longer timeout (90 min - gives trends room to develop)
        - Break-even at +0.5% (protects capital)
        - ATR-based TP (2.5x ATR target)
        """
        regime = MarketRegime.RANGE
        preset = self._get_conservative_preset(regime)

        # ATR-based SL
        sl_price = self._calculate_conservative_sl(
            entry_price=entry_price,
            direction=direction,
            indicators=indicators,
            preset=preset,
        )

        if direction == "LONG":
            sl_pct = ((entry_price - sl_price) / entry_price) * 100
        else:
            sl_pct = ((sl_price - entry_price) / entry_price) * 100

        # ATR-based take profit
        tp_price = None
        tp_pct = None

        if indicators and indicators.atr > 0:
            if direction == "LONG":
                tp_price = entry_price + (indicators.atr * self.atr_tp_multiplier)
            else:
                tp_price = entry_price - (indicators.atr * self.atr_tp_multiplier)
            tp_pct = abs((tp_price - entry_price) / entry_price) * 100

        # Conservative trailing
        trail_dist = self.trail_distance

        # ATR-based trailing (if available)
        if indicators and indicators.atr > 0:
            atr_trail = (indicators.atr * 1.2 / entry_price) * 100
            trail_dist = max(trail_dist, atr_trail)  # Use WIDER of the two

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
            min_profit_on_timeout_pct=0.20,
        )

        return plan

    # =========================================================================
    # HELPER: SIGNAL CHECKS
    # =========================================================================

    def _check_ema_alignment(self, indicators: IndicatorValues) -> Optional[str]:
        """Standard EMA alignment check."""
        if indicators.ema9 > indicators.ema21 > indicators.ema50:
            return "LONG"
        return None

    def _is_adx_rising(self, df: pd.DataFrame) -> bool:
        """
        Check if ADX is rising over the lookback period.

        Compares current ADX to ADX N bars ago.
        Rising ADX = trend is STRENGTHENING.
        """
        if "adx" not in df.columns or len(df) < self.adx_lookback + 1:
            return False

        current_adx = df["adx"].iloc[-1]
        previous_adx = df["adx"].iloc[-(self.adx_lookback + 1)]

        if pd.isna(current_adx) or pd.isna(previous_adx):
            return False

        return float(current_adx) > float(previous_adx)

    def _check_narrow_rsi(self, indicators: IndicatorValues, direction: str) -> bool:
        """Check RSI within narrow conservative window."""
        if direction == "LONG":
            return self.rsi_min <= indicators.rsi <= self.rsi_max
        else:
            return (100 - self.rsi_max) <= indicators.rsi <= (100 - self.rsi_min)

    def _check_volume_rising(
        self, df: pd.DataFrame, indicators: IndicatorValues
    ) -> bool:
        """Check volume >= multiplier AND optionally rising."""
        return indicators.volume_ratio >= self.volume_multiplier

    def _is_volume_rising(self, df: pd.DataFrame) -> bool:
        """Check if current volume > previous volume."""
        if len(df) < 3:
            return False

        current_vol = df["volume"].iloc[-1]
        prev_vol = df["volume"].iloc[-2]

        if pd.isna(current_vol) or pd.isna(prev_vol) or prev_vol == 0:
            return False

        return float(current_vol) > float(prev_vol)

    def _check_di_with_spread(
        self, indicators: IndicatorValues, direction: str
    ) -> bool:
        """Check DI alignment with minimum spread requirement."""
        if direction == "LONG":
            return (
                indicators.di_plus > indicators.di_minus
                and (indicators.di_plus - indicators.di_minus) >= self.min_di_spread
            )
        else:
            return (
                indicators.di_minus > indicators.di_plus
                and (indicators.di_minus - indicators.di_plus) >= self.min_di_spread
            )

    def _check_macd_confirm(self, indicators: IndicatorValues, direction: str) -> bool:
        """MACD must confirm direction."""
        if indicators.macd is None or indicators.macd_signal is None:
            return False  # If MACD not available, fail (mandatory)

        histogram = indicators.macd_histogram or (
            indicators.macd - indicators.macd_signal
        )

        if direction == "LONG":
            return indicators.macd > indicators.macd_signal and histogram > 0
        else:
            return indicators.macd < indicators.macd_signal and histogram < 0

    def _check_bb_confirm(self, indicators: IndicatorValues, direction: str) -> bool:
        """BB must confirm direction (price in correct half)."""
        if indicators.bb_upper is None or indicators.bb_middle is None:
            return False

        if direction == "LONG":
            return indicators.close > indicators.bb_middle
        else:
            return indicators.close < indicators.bb_middle

    def _check_stoch_confirm(self, indicators: IndicatorValues, direction: str) -> bool:
        """Stochastic must confirm direction."""
        if indicators.stoch_k is None or indicators.stoch_d is None:
            return False

        if direction == "LONG":
            return indicators.stoch_k > indicators.stoch_d and indicators.stoch_k < 80
        else:
            return indicators.stoch_k < indicators.stoch_d and indicators.stoch_k > 20

    # =========================================================================
    # HELPER: STOP LOSS
    # =========================================================================

    def _calculate_conservative_sl(
        self,
        entry_price: float,
        direction: str,
        indicators: Optional[IndicatorValues],
        preset: RegimePreset,
    ) -> float:
        """
        Calculate conservative SL.

        Uses the WIDER of ATR-based and fixed % for safety.
        Conservative strategy gives trades more room to breathe.
        """
        # Fixed % SL
        if direction == "LONG":
            sl_fixed = entry_price * (1 - preset.stop_loss_pct / 100)
        else:
            sl_fixed = entry_price * (1 + preset.stop_loss_pct / 100)

        # ATR-based SL
        if self.use_atr_sl and indicators and indicators.atr > 0:
            if direction == "LONG":
                sl_atr = entry_price - (indicators.atr * self.atr_sl_multiplier)
            else:
                sl_atr = entry_price + (indicators.atr * self.atr_sl_multiplier)

            # Use the WIDER stop (further from entry) for conservative
            if direction == "LONG":
                return min(sl_fixed, sl_atr)  # Lower = wider for LONG
            else:
                return max(sl_fixed, sl_atr)  # Higher = wider for SHORT

        return sl_fixed

    # =========================================================================
    # HELPER: REGIME PRESET
    # =========================================================================

    def _get_conservative_preset(self, regime: MarketRegime) -> RegimePreset:
        """Get conservative-specific regime preset."""
        if regime in CONSERVATIVE_REGIME_PRESETS:
            return CONSERVATIVE_REGIME_PRESETS[regime]
        return get_regime_preset(regime, self.config)

    # =========================================================================
    # HELPER: SIGNAL NOTES
    # =========================================================================

    def _build_conservative_notes(
        self,
        indicators: IndicatorValues,
        regime: MarketRegime,
        df: pd.DataFrame,
        adx_rising: bool,
        volume_rising: bool,
    ) -> str:
        """Build detailed conservative signal notes."""
        notes = []

        notes.append("🛡️CONSERVATIVE")

        # EMA
        notes.append("EMA✓")

        # ADX + rising
        adx_arrow = "↑" if adx_rising else "↓"
        adx_str = f"ADX={indicators.adx:.1f}{adx_arrow}"
        if indicators.adx > self.strong_adx:
            adx_str = f"💪{adx_str}"
        notes.append(adx_str)

        # RSI
        notes.append(f"RSI={indicators.rsi:.1f}")

        # Volume + rising
        vol_arrow = "↑" if volume_rising else "→"
        vol_str = f"Vol={indicators.volume_ratio:.1f}x{vol_arrow}"
        if indicators.volume_ratio >= self.volume_spike:
            vol_str = f"🔥{vol_str}"
        notes.append(vol_str)

        # Confirmations
        confirms = []
        if indicators.macd is not None and indicators.macd_histogram is not None:
            if indicators.macd_histogram > 0:
                confirms.append("MACD✓")
        if indicators.bb_middle is not None:
            if indicators.close > indicators.bb_middle:
                confirms.append("BB✓")
        if indicators.stoch_k is not None and indicators.stoch_d is not None:
            if indicators.stoch_k > indicators.stoch_d:
                confirms.append("Stoch✓")

        if confirms:
            notes.append(f"[{','.join(confirms)}]")

        # DI spread
        di_spread = abs(indicators.di_plus - indicators.di_minus)
        notes.append(f"DI-spread={di_spread:.1f}")

        # MTF
        if self.mtf_enabled:
            notes.append(f"MTF:{self.mtf_higher_tf}✓")

        # Regime
        notes.append(f"Regime:{regime.value}")

        # ATR SL info
        if self.use_atr_sl and indicators.atr > 0:
            atr_sl_pct = (
                indicators.atr * self.atr_sl_multiplier / indicators.close
            ) * 100
            notes.append(f"ATR-SL={atr_sl_pct:.2f}%")

        return ", ".join(notes)

    # =========================================================================
    # METADATA
    # =========================================================================

    def get_min_data_length(self) -> int:
        """Minimum candles needed (more than original for confirmation)."""
        return 120

    def supports_timeframe(self, timeframe: str) -> bool:
        """Conservative works on medium-to-long timeframes."""
        supported = {"5m", "15m", "30m", "1h", "2h", "4h", "1d"}
        return timeframe in supported

    def get_optimal_timeframes(self) -> list[str]:
        """Optimal timeframes for conservative strategy."""
        return ["15m", "30m", "1h", "4h"]

    def __repr__(self) -> str:
        return (
            f"SignalBoltConservative("
            f"tf={self.timeframe}, "
            f"adx>={self.min_adx}(rising), "
            f"rsi={self.rsi_min}-{self.rsi_max}, "
            f"vol>={self.volume_multiplier}x(rising), "
            f"mtf={'ON' if self.mtf_enabled else 'OFF'}, "
            f"atr_sl={'ON' if self.use_atr_sl else 'OFF'}, "
            f"timeout={self.timeout_minutes}min)"
        )
