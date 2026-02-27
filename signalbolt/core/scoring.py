"""
Signal scoring system (0-120+ points).

CORE scoring (0-100):
- EMA alignment: 20%
- ADX strength: 30%
- RSI: 15%
- Volume: 20%
- Price position: 10%
- DI spread: 5%

BONUS scoring (0-20+):
- MACD: +5-10
- Bollinger Bands: +5
- Stochastic: +5
"""

from dataclasses import dataclass
from typing import Optional
from signalbolt.core.indicators import IndicatorValues


# =============================================================================
# SCORE BREAKDOWN
# =============================================================================

@dataclass
class ScoreBreakdown:
    """Detailed score breakdown for transparency."""
    
    # Core components (0-100)
    ema_alignment: float = 0.0
    adx_strength: float = 0.0
    rsi: float = 0.0
    volume: float = 0.0
    price_position: float = 0.0
    di_spread: float = 0.0
    
    # Bonus components (0-20+)
    macd_bonus: float = 0.0
    bb_bonus: float = 0.0
    stoch_bonus: float = 0.0
    
    @property
    def core_total(self) -> float:
        """Total core score (0-100)."""
        return (
            self.ema_alignment +
            self.adx_strength +
            self.rsi +
            self.volume +
            self.price_position +
            self.di_spread
        )
    
    @property
    def bonus_total(self) -> float:
        """Total bonus score (0-20+)."""
        return self.macd_bonus + self.bb_bonus + self.stoch_bonus
    
    @property
    def total(self) -> float:
        """Total score (can exceed 100)."""
        return self.core_total + self.bonus_total
    
    def to_dict(self) -> dict:
        """Convert to dict for display."""
        return {
            'core': {
                'ema_alignment': round(self.ema_alignment, 1),
                'adx_strength': round(self.adx_strength, 1),
                'rsi': round(self.rsi, 1),
                'volume': round(self.volume, 1),
                'price_position': round(self.price_position, 1),
                'di_spread': round(self.di_spread, 1),
                'total': round(self.core_total, 1),
            },
            'bonus': {
                'macd': round(self.macd_bonus, 1),
                'bollinger': round(self.bb_bonus, 1),
                'stochastic': round(self.stoch_bonus, 1),
                'total': round(self.bonus_total, 1),
            },
            'total_score': round(self.total, 1),
        }
    
    def format_telegram(self) -> str:
        """Format for Telegram message."""
        msg = f"<b>Score: {self.total:.1f}/100</b>\n\n"
        
        msg += "<b>📊 Core Components:</b>\n"
        msg += f"  EMA Alignment: {self.ema_alignment:.1f}/20\n"
        msg += f"  ADX Strength: {self.adx_strength:.1f}/30\n"
        msg += f"  RSI: {self.rsi:.1f}/15\n"
        msg += f"  Volume: {self.volume:.1f}/20\n"
        msg += f"  Price Position: {self.price_position:.1f}/10\n"
        msg += f"  DI Spread: {self.di_spread:.1f}/5\n"
        msg += f"  <i>Subtotal: {self.core_total:.1f}/100</i>\n"
        
        if self.bonus_total > 0:
            msg += f"\n<b>✨ Bonus:</b>\n"
            if self.macd_bonus > 0:
                msg += f"  MACD: +{self.macd_bonus:.1f}\n"
            if self.bb_bonus > 0:
                msg += f"  Bollinger: +{self.bb_bonus:.1f}\n"
            if self.stoch_bonus > 0:
                msg += f"  Stochastic: +{self.stoch_bonus:.1f}\n"
        
        return msg


# =============================================================================
# SIGNAL SCORER
# =============================================================================

class SignalScorer:
    """
    Calculate signal score from indicator values.
    
    Usage:
        scorer = SignalScorer()
        breakdown = scorer.score(indicators, direction='LONG')
        
        if breakdown.total >= 70:
            # Good signal
    """
    
    def __init__(
        self,
        enable_macd: bool = True,
        enable_bb: bool = True,
        enable_stoch: bool = True
    ):
        """
        Initialize scorer.
        
        Args:
            enable_macd: Use MACD bonus
            enable_bb: Use Bollinger Bands bonus
            enable_stoch: Use Stochastic bonus
        """
        self.enable_macd = enable_macd
        self.enable_bb = enable_bb
        self.enable_stoch = enable_stoch
    
    def score(
        self,
        ind: IndicatorValues,
        direction: str = 'LONG'
    ) -> ScoreBreakdown:
        """
        Calculate full score breakdown.
        
        Args:
            ind: Indicator values
            direction: 'LONG' or 'SHORT'
        
        Returns:
            ScoreBreakdown instance
        """
        breakdown = ScoreBreakdown()
        
        # Core scoring
        breakdown.ema_alignment = self._score_ema_alignment(ind, direction)
        breakdown.adx_strength = self._score_adx_strength(ind)
        breakdown.rsi = self._score_rsi(ind, direction)
        breakdown.volume = self._score_volume(ind)
        breakdown.price_position = self._score_price_position(ind, direction)
        breakdown.di_spread = self._score_di_spread(ind)
        
        # Bonus scoring
        if self.enable_macd and ind.macd is not None:
            breakdown.macd_bonus = self._score_macd(ind, direction)
        
        if self.enable_bb and ind.bb_upper is not None:
            breakdown.bb_bonus = self._score_bollinger(ind, direction)
        
        if self.enable_stoch and ind.stoch_k is not None:
            breakdown.stoch_bonus = self._score_stochastic(ind, direction)
        
        return breakdown
    
    # =========================================================================
    # CORE SCORING COMPONENTS
    # =========================================================================
    
    def _score_ema_alignment(self, ind: IndicatorValues, direction: str) -> float:
        """
        EMA alignment score (0-20 points).
        
        Tweaked: Stronger emphasis on wide gaps.
        """
        ema9 = ind.ema9
        ema21 = ind.ema21
        ema50 = ind.ema50
        
        if ema21 == 0:
            return 0.0
        
        # Check alignment
        if direction == 'LONG':
            aligned = (ema9 > ema21 > ema50)
        else:
            aligned = (ema9 < ema21 < ema50)
        
        if not aligned:
            return 0.0
        
        # Calculate gap
        ema_gap = abs(ema9 - ema21) / ema21 * 100
        
        # Scoring (tweaked - higher rewards for wider gaps)
        if ema_gap > 1.0:
            return 20.0
        elif ema_gap > 0.7:
            return 18.0
        elif ema_gap > 0.5:
            return 16.0
        elif ema_gap > 0.3:
            return 13.0
        elif ema_gap > 0.15:
            return 9.0
        else:
            return 5.0
    
    def _score_adx_strength(self, ind: IndicatorValues) -> float:
        """
        ADX strength score (0-30 points).
        
        Tweaked: Slightly higher thresholds for top scores.
        """
        adx = ind.adx
        
        if adx >= 55:
            return 30.0
        elif adx >= 45:
            return 27.0
        elif adx >= 38:
            return 24.0
        elif adx >= 32:
            return 20.0
        elif adx >= 26:
            return 15.0
        elif adx >= 20:
            return 10.0
        elif adx >= 15:
            return 5.0
        else:
            return 0.0
    
    def _score_rsi(self, ind: IndicatorValues, direction: str) -> float:
        """
        RSI score (0-15 points).
        
        Tweaked: Reward "healthy" RSI more.
        """
        rsi = ind.rsi
        
        if direction == 'LONG':
            # Perfect zone: 45-58
            if 48 <= rsi <= 56:
                return 15.0
            elif 45 <= rsi <= 60:
                return 12.0
            elif 42 <= rsi <= 65:
                return 9.0
            elif 38 <= rsi <= 68:
                return 5.0
            elif rsi > 75:  # Overbought
                return 0.0
            else:
                return 2.0
        
        else:  # SHORT
            if 44 <= rsi <= 52:
                return 15.0
            elif 40 <= rsi <= 55:
                return 12.0
            elif 35 <= rsi <= 58:
                return 9.0
            elif 32 <= rsi <= 62:
                return 5.0
            elif rsi < 25:  # Oversold
                return 0.0
            else:
                return 2.0
    
    def _score_volume(self, ind: IndicatorValues) -> float:
        """
        Volume score (0-20 points).
        
        Tweaked: Higher weight, stronger requirements.
        """
        ratio = ind.volume_ratio
        
        if ratio >= 3.0:
            return 20.0
        elif ratio >= 2.5:
            return 18.0
        elif ratio >= 2.0:
            return 15.0
        elif ratio >= 1.5:
            return 12.0
        elif ratio >= 1.2:
            return 8.0
        elif ratio >= 1.0:
            return 4.0
        elif ratio >= 0.8:
            return 2.0
        else:
            return 0.0
    
    def _score_price_position(self, ind: IndicatorValues, direction: str) -> float:
        """
        Price position relative to EMAs (0-10 points).
        """
        price = ind.close
        ema9 = ind.ema9
        ema21 = ind.ema21
        ema50 = ind.ema50
        
        if direction == 'LONG':
            if price > ema9 > ema21 > ema50:
                return 10.0
            elif price > ema9 > ema21:
                return 7.0
            elif price > ema21:
                return 4.0
            else:
                return 0.0
        
        else:  # SHORT
            if price < ema9 < ema21 < ema50:
                return 10.0
            elif price < ema9 < ema21:
                return 7.0
            elif price < ema21:
                return 4.0
            else:
                return 0.0
    
    def _score_di_spread(self, ind: IndicatorValues) -> float:
        """
        DI spread score (0-5 points).
        
        Could be increased to 0-10 if needed.
        """
        di_spread = abs(ind.di_plus - ind.di_minus)
        
        if di_spread > 25:
            return 5.0
        elif di_spread > 20:
            return 4.0
        elif di_spread > 15:
            return 3.0
        elif di_spread > 10:
            return 2.0
        elif di_spread > 5:
            return 1.0
        else:
            return 0.0
    
    # =========================================================================
    # BONUS SCORING COMPONENTS
    # =========================================================================
    
    def _score_macd(self, ind: IndicatorValues, direction: str) -> float:
        """
        MACD bonus (0-10 points).
        
        Reward crossover confirmation.
        """
        if ind.macd is None or ind.macd_signal is None:
            return 0.0
        
        macd = ind.macd
        signal = ind.macd_signal
        histogram = ind.macd_histogram or (macd - signal)
        
        if direction == 'LONG':
            # MACD above signal = bullish
            if macd > signal and histogram > 0:
                # Strong crossover
                if histogram > abs(macd) * 0.1:
                    return 10.0
                else:
                    return 6.0
            elif macd > signal:
                return 3.0
            else:
                return 0.0
        
        else:  # SHORT
            if macd < signal and histogram < 0:
                if abs(histogram) > abs(macd) * 0.1:
                    return 10.0
                else:
                    return 6.0
            elif macd < signal:
                return 3.0
            else:
                return 0.0
    
    def _score_bollinger(self, ind: IndicatorValues, direction: str) -> float:
        """
        Bollinger Bands bonus (0-5 points).
        
        Reward breakout/bounce aligned with direction.
        """
        if ind.bb_upper is None:
            return 0.0
        
        price = ind.close
        upper = ind.bb_upper
        lower = ind.bb_lower
        middle = ind.bb_middle
        
        # Distance from bands
        bb_range = upper - lower
        if bb_range == 0:
            return 0.0
        
        if direction == 'LONG':
            # Price bouncing from lower or breaking above middle
            distance_from_lower = price - lower
            pct_position = distance_from_lower / bb_range
            
            if pct_position < 0.2:  # Near lower band (bounce setup)
                return 5.0
            elif price > middle and pct_position > 0.5:  # Breakout above middle
                return 4.0
            elif pct_position > 0.3:
                return 2.0
            else:
                return 0.0
        
        else:  # SHORT
            distance_from_upper = upper - price
            pct_position = distance_from_upper / bb_range
            
            if pct_position < 0.2:  # Near upper band
                return 5.0
            elif price < middle and pct_position > 0.5:
                return 4.0
            elif pct_position > 0.3:
                return 2.0
            else:
                return 0.0
    
    def _score_stochastic(self, ind: IndicatorValues, direction: str) -> float:
        """
        Stochastic bonus (0-5 points).
        
        Reward overbought/oversold confirmation.
        """
        if ind.stoch_k is None or ind.stoch_d is None:
            return 0.0
        
        k = ind.stoch_k
        d = ind.stoch_d
        
        if direction == 'LONG':
            # Oversold bounce (K crossing D upward from < 20)
            if k > d and k < 30:
                return 5.0
            elif k > d and k < 40:
                return 3.0
            elif k < 50:
                return 1.0
            else:
                return 0.0
        
        else:  # SHORT
            if k < d and k > 70:
                return 5.0
            elif k < d and k > 60:
                return 3.0
            elif k > 50:
                return 1.0
            else:
                return 0.0


# =============================================================================
# CONVENIENCE FUNCTION
# =============================================================================

def calculate_score(
    indicators: IndicatorValues,
    direction: str = 'LONG',
    enable_bonus: bool = True
) -> ScoreBreakdown:
    """
    Calculate signal score (convenience function).
    
    Args:
        indicators: Calculated indicators
        direction: 'LONG' or 'SHORT'
        enable_bonus: Use bonus indicators
    
    Returns:
        ScoreBreakdown with full details
    """
    scorer = SignalScorer(
        enable_macd=enable_bonus,
        enable_bb=enable_bonus,
        enable_stoch=enable_bonus
    )
    
    return scorer.score(indicators, direction)