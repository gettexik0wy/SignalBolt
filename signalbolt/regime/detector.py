"""
Market regime detection.

Detects current market regime based on:
- Price momentum (30-day, 7-day, 90-day)
- Drawdown from running peak
- Volatility
- Trend strength
- Macro context (prevents misclassifying bear bounces as range)

Key principle: NO LOOK-AHEAD BIAS
- Uses running peak (not future peak)
- Only historical data

Regimes:
- BULL: Strong uptrend, small drawdown
- BEAR: Downtrend or deep drawdown (even if recent bounce)
- RANGE: Sideways/consolidation (only in neutral macro context)
- CRASH: Fast drop or extreme drawdown
- RECOVERY: Bounce after crash, but still in deep drawdown

Usage:
    detector = RegimeDetector()

    # Detect from DataFrame
    regime = detector.detect(df, current_idx)

    # Detect from symbol (fetches data)
    regime = detector.detect_symbol('BTCUSDT', exchange)

    # Get detailed metrics
    metrics = detector.get_metrics(df, current_idx)
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple, List
from dataclasses import dataclass
from enum import Enum

from signalbolt.core.config import Config
from signalbolt.exchange.base import ExchangeBase
from signalbolt.data.manager import DataManager
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.regime.detector")


# =============================================================================
# REGIME ENUM
# =============================================================================


class MarketRegime(Enum):
    """Market regime types."""

    BULL = "bull"
    BEAR = "bear"
    RANGE = "range"
    CRASH = "crash"
    RECOVERY = "recovery"  # NEW: Bounce after crash but still in deep DD
    UNKNOWN = "unknown"

    @property
    def emoji(self) -> str:
        """Get emoji for regime."""
        emojis = {
            "bull": "🟢",
            "bear": "🔴",
            "range": "🟡",
            "crash": "💥",
            "recovery": "🔄",
            "unknown": "❓",
        }
        return emojis.get(self.value, "❓")

    @property
    def description(self) -> str:
        """Get description."""
        descriptions = {
            "bull": "Bullish - Strong uptrend",
            "bear": "Bearish - Downtrend or deep drawdown",
            "range": "Ranging - Sideways consolidation",
            "crash": "Crash - Sharp decline",
            "recovery": "Recovery - Bouncing from deep drawdown",
            "unknown": "Unknown - Insufficient data",
        }
        return descriptions.get(self.value, "Unknown")


# =============================================================================
# REGIME METRICS
# =============================================================================


@dataclass
class RegimeMetrics:
    """Detailed regime metrics."""

    # Price changes
    price_change_7d_pct: float = 0.0
    price_change_30d_pct: float = 0.0
    price_change_90d_pct: float = 0.0

    # Trend
    trend_strength: float = 0.0  # 0-100
    trend_direction: str = "neutral"  # "up", "down", "neutral"

    # Volatility
    volatility_7d_pct: float = 0.0
    volatility_30d_pct: float = 0.0
    volatility_percentile: float = 0.0  # vs historical

    # Drawdown
    current_drawdown_pct: float = 0.0
    max_drawdown_30d_pct: float = 0.0
    running_peak: float = 0.0

    # Moving averages
    price_vs_sma20_pct: float = 0.0
    price_vs_sma50_pct: float = 0.0
    price_vs_sma200_pct: float = 0.0
    sma_alignment: str = "neutral"  # "bullish", "bearish", "neutral"

    # Volume
    volume_trend: str = "neutral"  # "increasing", "decreasing", "neutral"
    volume_ratio: float = 1.0

    # Macro context (NEW)
    macro_trend: str = "neutral"  # "bull", "bear", "neutral"
    is_deep_drawdown: bool = False
    is_recovering: bool = False

    # Result
    regime: MarketRegime = MarketRegime.UNKNOWN
    confidence: float = 0.0  # 0-100

    def to_dict(self) -> dict:
        return {
            "price_change_7d_pct": round(self.price_change_7d_pct, 2),
            "price_change_30d_pct": round(self.price_change_30d_pct, 2),
            "price_change_90d_pct": round(self.price_change_90d_pct, 2),
            "trend_strength": round(self.trend_strength, 1),
            "trend_direction": self.trend_direction,
            "volatility_7d_pct": round(self.volatility_7d_pct, 2),
            "volatility_30d_pct": round(self.volatility_30d_pct, 2),
            "current_drawdown_pct": round(self.current_drawdown_pct, 2),
            "max_drawdown_30d_pct": round(self.max_drawdown_30d_pct, 2),
            "running_peak": round(self.running_peak, 8),
            "price_vs_sma20_pct": round(self.price_vs_sma20_pct, 2),
            "price_vs_sma50_pct": round(self.price_vs_sma50_pct, 2),
            "sma_alignment": self.sma_alignment,
            "volume_trend": self.volume_trend,
            "macro_trend": self.macro_trend,
            "is_deep_drawdown": self.is_deep_drawdown,
            "is_recovering": self.is_recovering,
            "regime": self.regime.value,
            "confidence": round(self.confidence, 1),
        }


# =============================================================================
# REGIME DETECTOR
# =============================================================================


class RegimeDetector:
    """
    Detect market regime without look-ahead bias.

    FIXED: Now properly handles bear market bounces.

    Key changes:
    - Deep drawdown context prevents "range" misclassification
    - Macro trend analysis (90d + drawdown context)
    - Hysteresis to prevent rapid regime flipping
    - Recovery phase detection

    Usage:
        detector = RegimeDetector()

        # From DataFrame at specific index
        regime = detector.detect(df, current_idx)

        # With metrics
        metrics = detector.get_metrics(df, current_idx)
        print(f"Regime: {metrics.regime.value}, Confidence: {metrics.confidence}%")

        # For live trading (fetches data)
        regime = detector.detect_live('BTCUSDT', exchange)
    """

    def __init__(
        self,
        config: Optional[Config] = None,
        lookback_days: int = 45,
        bull_threshold: float = 25.0,
        bear_threshold: float = -15.0,
        crash_threshold_7d: float = -20.0,
        crash_threshold_dd: float = -25.0,
        deep_dd_threshold: float = -40.0,  # NEW
        moderate_dd_threshold: float = -20.0,  # NEW
        candles_per_day: int = 288,  # 5m candles
    ):
        """
        Initialize detector.

        Args:
            config: Config instance (overrides other params)
            lookback_days: Days to look back for analysis
            bull_threshold: 30d change % for bull
            bear_threshold: 30d change % for bear
            crash_threshold_7d: 7d change % for crash
            crash_threshold_dd: Drawdown % for crash
            deep_dd_threshold: Drawdown % for deep bear (NEW)
            moderate_dd_threshold: Drawdown % for moderate bear (NEW)
            candles_per_day: Candles per day (288 for 5m)
        """
        self.config = config

        # Load from config or use defaults
        if config:
            self.lookback_days = config.get(
                "regime", "lookback_days", default=lookback_days
            )
            self.bull_threshold = config.get(
                "regime", "bull_threshold", default=bull_threshold
            )
            self.bear_threshold = config.get(
                "regime", "bear_threshold", default=bear_threshold
            )
            self.crash_threshold_7d = config.get(
                "regime", "crash_threshold", default=crash_threshold_7d
            )
            self.crash_threshold_dd = config.get(
                "regime", "crash_drawdown", default=crash_threshold_dd
            )
            self.deep_dd_threshold = config.get(
                "regime", "deep_dd_threshold", default=deep_dd_threshold
            )
            self.moderate_dd_threshold = config.get(
                "regime", "moderate_dd_threshold", default=moderate_dd_threshold
            )
        else:
            self.lookback_days = lookback_days
            self.bull_threshold = bull_threshold
            self.bear_threshold = bear_threshold
            self.crash_threshold_7d = crash_threshold_7d
            self.crash_threshold_dd = crash_threshold_dd
            self.deep_dd_threshold = deep_dd_threshold
            self.moderate_dd_threshold = moderate_dd_threshold

        self.candles_per_day = candles_per_day

        # Running state for NO LOOK-AHEAD
        self._running_peak: Optional[float] = None

        # Hysteresis (NEW)
        self._regime_history: List[MarketRegime] = []
        self._regime_hold_count: int = 0
        self._min_hold_confirmations: int = 3  # Need 3 detections to switch

        # Cache
        self._last_regime: MarketRegime = MarketRegime.UNKNOWN
        self._last_detection_time: Optional[datetime] = None
        self._cache_ttl = timedelta(hours=1)

        log.info(
            f"RegimeDetector initialized (lookback={self.lookback_days}d, "
            f"bull>{self.bull_threshold}%, bear<{self.bear_threshold}%, "
            f"deep_dd<{self.deep_dd_threshold}%)"
        )

    # =========================================================================
    # MAIN DETECTION
    # =========================================================================

    def detect(
        self,
        df: pd.DataFrame,
        current_idx: Optional[int] = None,
        use_hysteresis: bool = True,  # NEW
    ) -> MarketRegime:
        """
        Detect regime at current point.

        Args:
            df: OHLCV DataFrame
            current_idx: Current index (None = last row)
            use_hysteresis: Use hysteresis to prevent rapid switching

        Returns:
            MarketRegime
        """
        metrics = self.get_metrics(df, current_idx, use_hysteresis=use_hysteresis)
        return metrics.regime

    def get_metrics(
        self,
        df: pd.DataFrame,
        current_idx: Optional[int] = None,
        use_hysteresis: bool = True,
    ) -> RegimeMetrics:
        """
        Get detailed regime metrics.

        Args:
            df: OHLCV DataFrame
            current_idx: Current index (None = last row)
            use_hysteresis: Use hysteresis

        Returns:
            RegimeMetrics with all calculations
        """
        if current_idx is None:
            current_idx = len(df) - 1

        metrics = RegimeMetrics()

        # Calculate lookback windows
        lookback_candles = self.lookback_days * self.candles_per_day
        week_candles = 7 * self.candles_per_day
        month_candles = 30 * self.candles_per_day
        quarter_candles = 90 * self.candles_per_day

        # Safety check
        if current_idx < lookback_candles:
            metrics.regime = MarketRegime.UNKNOWN
            metrics.confidence = 0.0
            return metrics

        # Get data windows
        full_window = df.iloc[max(0, current_idx - lookback_candles) : current_idx + 1]
        week_window = df.iloc[max(0, current_idx - week_candles) : current_idx + 1]
        month_window = df.iloc[max(0, current_idx - month_candles) : current_idx + 1]

        if len(full_window) < 100:
            metrics.regime = MarketRegime.UNKNOWN
            return metrics

        current_price = float(full_window["close"].iloc[-1])

        # === PRICE CHANGES ===
        if len(week_window) > 0:
            week_ago_price = float(week_window["close"].iloc[0])
            metrics.price_change_7d_pct = ((current_price / week_ago_price) - 1) * 100

        if len(month_window) > 0:
            month_ago_price = float(month_window["close"].iloc[0])
            metrics.price_change_30d_pct = ((current_price / month_ago_price) - 1) * 100

        # 90d change (if enough data)
        if current_idx >= quarter_candles:
            quarter_window = df.iloc[current_idx - quarter_candles : current_idx + 1]
            quarter_ago_price = float(quarter_window["close"].iloc[0])
            metrics.price_change_90d_pct = (
                (current_price / quarter_ago_price) - 1
            ) * 100

        # === VOLATILITY ===
        returns_7d = week_window["close"].pct_change().dropna()
        returns_30d = month_window["close"].pct_change().dropna()

        if len(returns_7d) > 0:
            metrics.volatility_7d_pct = float(
                returns_7d.std() * 100 * np.sqrt(self.candles_per_day)
            )

        if len(returns_30d) > 0:
            metrics.volatility_30d_pct = float(
                returns_30d.std() * 100 * np.sqrt(self.candles_per_day)
            )

        # === RUNNING PEAK & DRAWDOWN (NO LOOK-AHEAD) ===
        # Initialize running peak if needed
        if self._running_peak is None:
            # Use max from lookback window
            self._running_peak = float(full_window["close"].max())

        # Update running peak (only goes up, never uses future data)
        if current_price > self._running_peak:
            self._running_peak = current_price

        metrics.running_peak = self._running_peak
        metrics.current_drawdown_pct = ((current_price / self._running_peak) - 1) * 100

        # Max drawdown in last 30 days (using running peak, not future peak)
        if len(month_window) > 0:
            running_max = month_window["close"].expanding().max()
            drawdowns = (month_window["close"] / running_max - 1) * 100
            metrics.max_drawdown_30d_pct = float(drawdowns.min())

        # === DRAWDOWN CONTEXT (NEW) ===
        metrics.is_deep_drawdown = metrics.current_drawdown_pct < self.deep_dd_threshold

        # === MOVING AVERAGES ===
        if len(full_window) >= 20:
            sma20 = float(full_window["close"].tail(20).mean())
            metrics.price_vs_sma20_pct = ((current_price / sma20) - 1) * 100

        if len(full_window) >= 50:
            sma50 = float(full_window["close"].tail(50).mean())
            metrics.price_vs_sma50_pct = ((current_price / sma50) - 1) * 100

        # SMA200 (adjusted for 5m timeframe - use 200 candles, not 200 days)
        if len(full_window) >= 200:
            sma200 = float(full_window["close"].tail(200).mean())
            metrics.price_vs_sma200_pct = ((current_price / sma200) - 1) * 100

        # SMA alignment
        if metrics.price_vs_sma20_pct > 0 and metrics.price_vs_sma50_pct > 0:
            metrics.sma_alignment = "bullish"
        elif metrics.price_vs_sma20_pct < 0 and metrics.price_vs_sma50_pct < 0:
            metrics.sma_alignment = "bearish"
        else:
            metrics.sma_alignment = "neutral"

        # === TREND STRENGTH ===
        if len(month_window) >= 30:
            daily_returns = month_window["close"].pct_change().dropna()

            if len(daily_returns) > 0:
                positive_days = (daily_returns > 0).sum()
                negative_days = (daily_returns < 0).sum()
                total_days = len(daily_returns)

                if positive_days > negative_days:
                    metrics.trend_direction = "up"
                    metrics.trend_strength = (positive_days / total_days) * 100
                elif negative_days > positive_days:
                    metrics.trend_direction = "down"
                    metrics.trend_strength = (negative_days / total_days) * 100
                else:
                    metrics.trend_direction = "neutral"
                    metrics.trend_strength = 50.0

        # === VOLUME ANALYSIS ===
        if "volume" in full_window.columns:
            # Compare recent 7d volume vs older 7d volume
            recent_vol = float(full_window["volume"].tail(week_candles).mean())
            older_vol_window = full_window["volume"].iloc[
                -(week_candles * 2) : -week_candles
            ]

            if len(older_vol_window) > 0:
                older_vol = float(older_vol_window.mean())

                if older_vol > 0:
                    metrics.volume_ratio = recent_vol / older_vol

                    if metrics.volume_ratio > 1.2:
                        metrics.volume_trend = "increasing"
                    elif metrics.volume_ratio < 0.8:
                        metrics.volume_trend = "decreasing"
                    else:
                        metrics.volume_trend = "neutral"

        # === MACRO TREND (NEW) ===
        metrics.macro_trend = self._get_macro_trend(metrics)

        # === RECOVERY DETECTION (NEW) ===
        metrics.is_recovering = (
            metrics.is_deep_drawdown  # Still in deep DD
            and metrics.price_change_30d_pct > 15  # But bouncing
            and metrics.trend_direction == "up"
        )

        # === DETERMINE REGIME ===
        regime, confidence = self._classify_regime(metrics)

        # Apply hysteresis if enabled
        if use_hysteresis:
            regime, confidence = self._apply_hysteresis(regime, confidence)

        metrics.regime = regime
        metrics.confidence = confidence

        return metrics

    # =========================================================================
    # MACRO TREND ANALYSIS (NEW)
    # =========================================================================

    def _get_macro_trend(self, metrics: RegimeMetrics) -> str:
        """
        Determine macro trend (ignoring short-term bounces).

        This prevents misclassifying bear market bounces as bull/range.

        Returns: "bull", "bear", or "neutral"
        """
        # Deep drawdown = macro bear (even if recent bounce)
        if metrics.current_drawdown_pct < self.deep_dd_threshold:
            return "bear"

        # 90d strong downtrend = macro bear
        if metrics.price_change_90d_pct < -20:
            return "bear"

        # Moderate drawdown + negative 90d = macro bear
        if (
            metrics.current_drawdown_pct < self.moderate_dd_threshold
            and metrics.price_change_90d_pct < -10
        ):
            return "bear"

        # 90d strong uptrend = macro bull
        if metrics.price_change_90d_pct > 30:
            return "bull"

        # Small DD + positive 90d = macro bull
        if metrics.current_drawdown_pct > -10 and metrics.price_change_90d_pct > 15:
            return "bull"

        return "neutral"

    # =========================================================================
    # REGIME CLASSIFICATION (REFACTORED)
    # =========================================================================

    def _classify_regime(self, metrics: RegimeMetrics) -> Tuple[MarketRegime, float]:
        """
        Classify regime based on metrics.

        FIXED: Now properly handles bear market context.

        Key changes:
        - Deep drawdown prevents "range" misclassification
        - Macro trend context added
        - Recovery phase detection
        - Higher weight for drawdown in bear detection

        Returns:
            (MarketRegime, confidence %)
        """
        confidence_scores = {
            MarketRegime.BULL: 0.0,
            MarketRegime.BEAR: 0.0,
            MarketRegime.RANGE: 0.0,
            MarketRegime.CRASH: 0.0,
            MarketRegime.RECOVERY: 0.0,
        }

        # === CRASH DETECTION (highest priority) ===
        # Fast drop in 7 days
        if metrics.price_change_7d_pct < self.crash_threshold_7d:
            confidence_scores[MarketRegime.CRASH] += 50

        # Deep drawdown
        if metrics.current_drawdown_pct < self.crash_threshold_dd:
            confidence_scores[MarketRegime.CRASH] += 40

        # High volatility during drop
        if metrics.price_change_7d_pct < -10 and metrics.volatility_7d_pct > 5:
            confidence_scores[MarketRegime.CRASH] += 20

        # Extreme volatility
        if metrics.volatility_7d_pct > 10:
            confidence_scores[MarketRegime.CRASH] += 15

        # === BEAR DETECTION (IMPROVED) ===
        # Negative 30d change
        if metrics.price_change_30d_pct < self.bear_threshold:
            confidence_scores[MarketRegime.BEAR] += 40
        elif metrics.price_change_30d_pct < self.bear_threshold * 0.5:
            confidence_scores[MarketRegime.BEAR] += 20

        # Bearish SMA alignment
        if metrics.sma_alignment == "bearish":
            confidence_scores[MarketRegime.BEAR] += 20

        # Downward trend
        if metrics.trend_direction == "down" and metrics.trend_strength > 60:
            confidence_scores[MarketRegime.BEAR] += 20

        # DRAWDOWN CONTEXT (INCREASED WEIGHT) ===
        # Moderate drawdown
        if self.moderate_dd_threshold < metrics.current_drawdown_pct < -10:
            confidence_scores[MarketRegime.BEAR] += 30  # was 15

        # Deep drawdown = strong bear signal (NEW!)
        if metrics.is_deep_drawdown:
            confidence_scores[MarketRegime.BEAR] += 50  # NEW!
            log.debug(
                f"Deep drawdown detected: {metrics.current_drawdown_pct:.1f}% - forcing BEAR context"
            )

        # MACRO TREND OVERRIDE (NEW!) ===
        if metrics.macro_trend == "bear":
            confidence_scores[MarketRegime.BEAR] += 40
            confidence_scores[MarketRegime.RANGE] -= 50  # Penalize range heavily!
            log.debug("Macro trend is BEAR - penalizing RANGE")

        # Negative 90d trend
        if metrics.price_change_90d_pct < -15:
            confidence_scores[MarketRegime.BEAR] += 25

        # === RECOVERY DETECTION (NEW) ===
        if metrics.is_recovering:
            confidence_scores[MarketRegime.RECOVERY] += 60
            confidence_scores[MarketRegime.BULL] += 20  # Some bull characteristics
            confidence_scores[MarketRegime.BEAR] += 30  # But still in bear context
            confidence_scores[MarketRegime.RANGE] -= 40
            log.debug("Recovery phase detected (bounce from deep DD)")

        # === BULL DETECTION ===
        # Strong 30d gain
        if metrics.price_change_30d_pct > self.bull_threshold:
            confidence_scores[MarketRegime.BULL] += 40
        elif metrics.price_change_30d_pct > self.bull_threshold * 0.5:
            confidence_scores[MarketRegime.BULL] += 20

        # Small drawdown (healthy bull)
        if metrics.current_drawdown_pct > -5:
            confidence_scores[MarketRegime.BULL] += 20

        # Bullish SMA alignment
        if metrics.sma_alignment == "bullish":
            confidence_scores[MarketRegime.BULL] += 20

        # Upward trend
        if metrics.trend_direction == "up" and metrics.trend_strength > 60:
            confidence_scores[MarketRegime.BULL] += 20

        # Macro trend boost
        if metrics.macro_trend == "bull":
            confidence_scores[MarketRegime.BULL] += 30

        # Positive 90d trend
        if metrics.price_change_90d_pct > 20:
            confidence_scores[MarketRegime.BULL] += 25

        # === RANGE DETECTION (RESTRICTED) ===
        # IMPORTANT: Range ONLY if macro is neutral and no deep DD!

        if metrics.macro_trend == "neutral" and not metrics.is_deep_drawdown:
            # Small price change
            if -10 < metrics.price_change_30d_pct < 10:
                confidence_scores[MarketRegime.RANGE] += 30

            # Neutral trend
            if metrics.trend_direction == "neutral":
                confidence_scores[MarketRegime.RANGE] += 20

            # Neutral SMA
            if metrics.sma_alignment == "neutral":
                confidence_scores[MarketRegime.RANGE] += 15

            # Low volatility
            if metrics.volatility_30d_pct < 2:
                confidence_scores[MarketRegime.RANGE] += 15

            # Very small drawdown
            if metrics.current_drawdown_pct > -5:
                confidence_scores[MarketRegime.RANGE] += 10

            # 90d flat
            if -10 < metrics.price_change_90d_pct < 10:
                confidence_scores[MarketRegime.RANGE] += 15

        else:
            # In bear/bull macro OR deep DD: heavily penalize range
            confidence_scores[MarketRegime.RANGE] -= 60
            log.debug(
                f"Penalizing RANGE: macro={metrics.macro_trend}, deep_dd={metrics.is_deep_drawdown}"
            )

        # === DETERMINE WINNER ===
        # Crash has priority if score is high enough
        if confidence_scores[MarketRegime.CRASH] >= 70:
            return MarketRegime.CRASH, min(100, confidence_scores[MarketRegime.CRASH])

        # Recovery has priority if detected
        if confidence_scores[MarketRegime.RECOVERY] >= 60:
            return MarketRegime.RECOVERY, min(
                100, confidence_scores[MarketRegime.RECOVERY]
            )

        # Find highest score
        max_regime = max(confidence_scores, key=confidence_scores.get)
        max_score = confidence_scores[max_regime]

        # Debug logging
        log.debug(
            f"Regime scores: BULL={confidence_scores[MarketRegime.BULL]:.0f}, "
            f"BEAR={confidence_scores[MarketRegime.BEAR]:.0f}, "
            f"RANGE={confidence_scores[MarketRegime.RANGE]:.0f}, "
            f"CRASH={confidence_scores[MarketRegime.CRASH]:.0f}, "
            f"RECOVERY={confidence_scores[MarketRegime.RECOVERY]:.0f}"
        )

        # If no clear winner, use macro context
        if max_score < 30:
            if metrics.is_deep_drawdown or metrics.macro_trend == "bear":
                log.debug("No clear winner, defaulting to BEAR due to context")
                return MarketRegime.BEAR, 50.0
            elif metrics.macro_trend == "bull":
                return MarketRegime.BULL, 50.0
            else:
                return MarketRegime.RANGE, 50.0

        # Minimum confidence threshold
        if max_score < 20:
            return MarketRegime.UNKNOWN, 0.0

        return max_regime, min(100, max_score)

    # =========================================================================
    # HYSTERESIS (NEW)
    # =========================================================================

    def _apply_hysteresis(
        self, new_regime: MarketRegime, confidence: float
    ) -> Tuple[MarketRegime, float]:
        """
        Apply hysteresis to prevent rapid regime switching.

        Requires multiple consecutive detections to confirm regime change.

        Args:
            new_regime: Newly detected regime
            confidence: Detection confidence

        Returns:
            (regime, confidence) - may return old regime if not confirmed
        """
        # If this is first detection
        if not self._regime_history:
            self._regime_history.append(new_regime)
            self._regime_hold_count = 0
            return new_regime, confidence

        current_regime = self._regime_history[-1]

        # If regime changed
        if new_regime != current_regime:
            self._regime_hold_count += 1

            # Need X consecutive detections to confirm switch
            if self._regime_hold_count >= self._min_hold_confirmations:
                # Confirmed switch
                self._regime_history.append(new_regime)
                self._regime_hold_count = 0

                log.info(
                    f"Regime switched: {current_regime.emoji} {current_regime.value} → "
                    f"{new_regime.emoji} {new_regime.value} (confidence: {confidence:.1f}%)"
                )

                return new_regime, confidence
            else:
                # Not confirmed yet, stay in old regime
                log.debug(
                    f"Regime pending: {new_regime.value} ({self._regime_hold_count}/{self._min_hold_confirmations})"
                )
                return current_regime, confidence * 0.7  # Lower confidence

        else:
            # Same regime, reset counter
            self._regime_hold_count = 0
            return current_regime, confidence

    # =========================================================================
    # LIVE DETECTION
    # =========================================================================

    def detect_live(
        self,
        symbol: str = "BTCUSDT",
        exchange: Optional[ExchangeBase] = None,
        data_manager: Optional[DataManager] = None,
    ) -> MarketRegime:
        """
        Detect regime for live trading.

        Args:
            symbol: Symbol to analyze (default BTC)
            exchange: Exchange instance
            data_manager: Data manager

        Returns:
            MarketRegime
        """
        # Check cache
        if self._last_detection_time:
            if datetime.now() - self._last_detection_time < self._cache_ttl:
                return self._last_regime

        # Get data
        if data_manager:
            df = data_manager.get_candles(
                symbol=symbol,
                interval="5m",
                limit=self.lookback_days * self.candles_per_day + 100,
            )
        elif exchange:
            from signalbolt.data.manager import DataManager

            dm = DataManager(mode="live", exchange=exchange)
            df = dm.get_candles(
                symbol, "5m", limit=self.lookback_days * self.candles_per_day + 100
            )
        else:
            log.warning("No exchange or data_manager provided")
            return MarketRegime.UNKNOWN

        if df is None or len(df) < 100:
            log.warning(f"Insufficient data for regime detection")
            return MarketRegime.UNKNOWN

        # Detect
        regime = self.detect(df, use_hysteresis=True)

        # Cache
        self._last_regime = regime
        self._last_detection_time = datetime.now()

        log.info(f"Regime detected: {regime.emoji} {regime.value.upper()}")

        return regime

    def detect_with_metrics_live(
        self,
        symbol: str = "BTCUSDT",
        exchange: Optional[ExchangeBase] = None,
        data_manager: Optional[DataManager] = None,
    ) -> RegimeMetrics:
        """
        Detect regime with full metrics for live trading.

        Returns:
            RegimeMetrics
        """
        if data_manager:
            df = data_manager.get_candles(
                symbol=symbol,
                interval="5m",
                limit=self.lookback_days * self.candles_per_day + 100,
            )
        elif exchange:
            from signalbolt.data.manager import DataManager

            dm = DataManager(mode="live", exchange=exchange)
            df = dm.get_candles(
                symbol, "5m", limit=self.lookback_days * self.candles_per_day + 100
            )
        else:
            return RegimeMetrics()

        if df is None or len(df) < 100:
            return RegimeMetrics()

        return self.get_metrics(df, use_hysteresis=True)

    # =========================================================================
    # RESET
    # =========================================================================

    def reset(self):
        """Reset detector state (running peak, hysteresis, cache)."""
        self._running_peak = None
        self._regime_history = []
        self._regime_hold_count = 0
        self._last_regime = MarketRegime.UNKNOWN
        self._last_detection_time = None

        log.debug("Detector state reset")

    # =========================================================================
    # UTILITY
    # =========================================================================

    def get_regime_summary(
        self, df: pd.DataFrame, current_idx: Optional[int] = None
    ) -> str:
        """
        Get human-readable regime summary.

        Returns:
            Formatted string
        """
        metrics = self.get_metrics(df, current_idx)

        lines = [
            f"{'=' * 50}",
            f"{metrics.regime.emoji} MARKET REGIME: {metrics.regime.value.upper()}",
            f"{'=' * 50}",
            f"",
            f"Confidence: {metrics.confidence:.1f}%",
            f"Macro Trend: {metrics.macro_trend.upper()}",
            f"",
            f"📈 Price Changes:",
            f"  7-day:  {metrics.price_change_7d_pct:+.2f}%",
            f"  30-day: {metrics.price_change_30d_pct:+.2f}%",
            f"  90-day: {metrics.price_change_90d_pct:+.2f}%",
            f"",
            f"📊 Trend:",
            f"  Direction: {metrics.trend_direction}",
            f"  Strength:  {metrics.trend_strength:.1f}%",
            f"  SMA Alignment: {metrics.sma_alignment}",
            f"",
            f"📉 Risk:",
            f"  Current Drawdown: {metrics.current_drawdown_pct:.2f}%",
            f"  30d Max Drawdown: {metrics.max_drawdown_30d_pct:.2f}%",
            f"  Deep DD: {'YES' if metrics.is_deep_drawdown else 'NO'}",
            f"  Recovering: {'YES' if metrics.is_recovering else 'NO'}",
            f"  Volatility (30d):  {metrics.volatility_30d_pct:.2f}%",
            f"",
            f"{'=' * 50}",
        ]

        return "\n".join(lines)


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================


def detect_regime(df: pd.DataFrame, config: Optional[Config] = None) -> MarketRegime:
    """Quick regime detection."""
    detector = RegimeDetector(config)
    return detector.detect(df)


def get_current_regime(
    exchange: ExchangeBase, symbol: str = "BTCUSDT", config: Optional[Config] = None
) -> MarketRegime:
    """Get current market regime for live trading."""
    detector = RegimeDetector(config)
    return detector.detect_live(symbol, exchange)


def create_detector(config: Optional[Config] = None) -> RegimeDetector:
    """Create regime detector instance."""
    return RegimeDetector(config)
