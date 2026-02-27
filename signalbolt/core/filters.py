"""
Signal filters - multi-layer filtering before trade execution.

CORE FILTERS (hard reject):
  ✅ RSI extremes (>75 LONG, <25 SHORT)
  ✅ ATR too low (<0.2%)
  ✅ Volume too low (<0.8x average)
  ✅ Spread too wide (>0.5%)

OPTIONAL FILTERS (warnings only):
  ⚠️ Weak trend (ADX < 15)
  ⚠️ Price far from EMAs
  ⚠️ High volatility spike
  ⚠️ Unusual volume spike
  ⚠️ Ultra tight spread (<0.1% - scalp alert)

All filters are configurable via config.yaml
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Set
from enum import Enum

from signalbolt.core.indicators import IndicatorValues
from signalbolt.core.scoring import ScoreBreakdown


# =============================================================================
# FILTER RESULT
# =============================================================================

class FilterStatus(Enum):
    """Filter result status."""
    PASS = "PASS"
    FAIL = "FAIL"
    WARNING = "WARNING"


@dataclass
class FilterResult:
    """Result of a single filter check."""
    
    name: str
    status: FilterStatus
    reason: Optional[str] = None
    value: Optional[float] = None
    threshold: Optional[float] = None
    
    @property
    def passed(self) -> bool:
        """Check if filter passed (PASS or WARNING)."""
        return self.status in [FilterStatus.PASS, FilterStatus.WARNING]
    
    @property
    def is_warning(self) -> bool:
        """Check if this is a warning."""
        return self.status == FilterStatus.WARNING
    
    def __repr__(self) -> str:
        if self.status == FilterStatus.PASS:
            return f"✓ {self.name}"
        elif self.status == FilterStatus.WARNING:
            return f"⚠️ {self.name}: {self.reason}"
        else:
            return f"✗ {self.name}: {self.reason}"


@dataclass
class FilterChainResult:
    """Result of entire filter chain."""
    
    symbol: str
    direction: str
    score: float
    results: List[FilterResult]
    filtered_at: datetime
    
    @property
    def passed(self) -> bool:
        """Check if all CORE filters passed (warnings are OK)."""
        return all(r.status != FilterStatus.FAIL for r in self.results)
    
    @property
    def has_warnings(self) -> bool:
        """Check if there are any warnings."""
        return any(r.is_warning for r in self.results)
    
    @property
    def failed_filters(self) -> List[FilterResult]:
        """Get list of failed filters (hard rejects only)."""
        return [r for r in self.results if r.status == FilterStatus.FAIL]
    
    @property
    def warnings(self) -> List[FilterResult]:
        """Get list of warnings."""
        return [r for r in self.results if r.is_warning]
    
    def summary(self) -> str:
        """Get human-readable summary."""
        if not self.passed:
            failed = ", ".join([r.name for r in self.failed_filters])
            return f"✗ {self.symbol} {self.direction} REJECTED: {failed}"
        elif self.has_warnings:
            warnings = ", ".join([r.name for r in self.warnings])
            return f"⚠️ {self.symbol} {self.direction} PASSED with warnings: {warnings}"
        else:
            return f"✓ {self.symbol} {self.direction} PASSED all filters (score: {self.score:.1f})"
    
    def to_dict(self) -> dict:
        """Convert to dict for logging."""
        return {
            'symbol': self.symbol,
            'direction': self.direction,
            'score': round(self.score, 1),
            'passed': self.passed,
            'has_warnings': self.has_warnings,
            'failed_filters': [r.name for r in self.failed_filters],
            'warnings': [r.name for r in self.warnings],
            'filtered_at': self.filtered_at.isoformat(),
        }


# =============================================================================
# SIGNAL FILTER
# =============================================================================

class SignalFilter:
    """
    Multi-layer signal filtering system.
    
    Usage:
        filter = SignalFilter(config)
        
        result = filter.check(
            symbol='BTCUSDT',
            direction='LONG',
            score=75.0,
            indicators=ind,
            breakdown=score_breakdown,
            spread_pct=0.12
        )
        
        if result.passed:
            if result.has_warnings:
                log.warning(f"Trade approved with warnings: {result.warnings}")
            # Execute trade
        else:
            log.info(f"Trade rejected: {result.summary()}")
    """
    
    def __init__(self, config):
        """
        Initialize filter.
        
        Args:
            config: Config instance
        """
        self.config = config
        
        # Cooldown tracking
        self._signal_history: Dict[str, datetime] = {}
        
        # Blacklist/Whitelist
        self._blacklist: Set[str] = set()
        self._whitelist: Set[str] = set()
        
        # Load settings
        self._load_settings()
    
    def _load_settings(self):
        """Load filter settings from config."""
        # CORE filters (hard reject)
        self.rsi_overbought = self.config.get('filters', 'rsi_overbought', default=75.0)
        self.rsi_oversold = self.config.get('filters', 'rsi_oversold', default=25.0)
        self.min_atr_pct = self.config.get('filters', 'min_atr_pct', default=0.2)
        self.min_volume_ratio = self.config.get('filters', 'min_volume_ratio', default=0.8)
        self.max_spread_pct = self.config.get('filters', 'max_spread_pct', default=0.5)
        
        # OPTIONAL filters (warnings)
        self.warn_adx_threshold = self.config.get('filters', 'warn_adx_threshold', default=15.0)
        self.warn_ema_distance_pct = self.config.get('filters', 'warn_ema_distance_pct', default=2.0)
        self.warn_high_volatility_pct = self.config.get('filters', 'warn_high_volatility_pct', default=5.0)
        self.warn_volume_spike_ratio = self.config.get('filters', 'warn_volume_spike_ratio', default=3.0)
        self.scalp_spread_pct = self.config.get('filters', 'scalp_spread_pct', default=0.1)
        
        # Risk filters
        self.signal_cooldown_min = self.config.get('scanner', 'signal_cooldown_min', default=30)
        self.max_positions = self.config.get('spot', 'max_positions', default=1)
        
        # Blacklist/Whitelist
        self._blacklist = set(self.config.get('filters', 'blacklist', default=[]))
        self._whitelist = set(self.config.get('filters', 'whitelist', default=[]))
    
    # =========================================================================
    # MAIN CHECK
    # =========================================================================
    
    def check(
        self,
        symbol: str,
        direction: str,
        score: float,
        indicators: IndicatorValues,
        breakdown: Optional[ScoreBreakdown] = None,
        current_positions: int = 0,
        spread_pct: Optional[float] = None
    ) -> FilterChainResult:
        """
        Run full filter chain.
        
        Args:
            symbol: Trading symbol
            direction: 'LONG' or 'SHORT'
            score: Signal score
            indicators: Indicator values
            breakdown: Score breakdown (optional)
            current_positions: Number of currently open positions
            spread_pct: Current bid/ask spread percentage
        
        Returns:
            FilterChainResult with all checks
        """
        results = []
        
        # =====================================================================
        # CORE FILTERS (HARD REJECT)
        # =====================================================================
        
        # Blacklist/Whitelist
        results.append(self._check_blacklist(symbol))
        results.append(self._check_whitelist(symbol))
        
        # RSI extremes
        results.append(self._check_rsi_extremes(indicators, direction))
        
        # ATR too low
        results.append(self._check_min_volatility(indicators))
        
        # Volume too low
        results.append(self._check_min_volume(indicators))
        
        # Spread too wide
        if spread_pct is not None:
            results.append(self._check_max_spread(spread_pct))
        
        # Cooldown
        results.append(self._check_cooldown(symbol))
        
        # Max positions
        results.append(self._check_max_positions(current_positions))
        
        # =====================================================================
        # OPTIONAL FILTERS (WARNINGS ONLY)
        # =====================================================================
        
        # Weak trend (ADX)
        results.append(self._check_weak_trend(indicators))
        
        # Price far from EMAs
        results.append(self._check_ema_distance(indicators, direction))
        
        # High volatility spike
        results.append(self._check_high_volatility(indicators))
        
        # Unusual volume spike
        results.append(self._check_volume_spike(indicators))
        
        # Ultra tight spread (scalp opportunity)
        if spread_pct is not None:
            results.append(self._check_scalp_spread(spread_pct))
        
        return FilterChainResult(
            symbol=symbol,
            direction=direction,
            score=score,
            results=results,
            filtered_at=datetime.now()
        )
    
    # =========================================================================
    # CORE FILTERS (HARD REJECT)
    # =========================================================================
    
    def _check_blacklist(self, symbol: str) -> FilterResult:
        """CORE: Check if symbol is blacklisted."""
        if not self._blacklist:
            return FilterResult('Blacklist', FilterStatus.PASS)
        
        if symbol in self._blacklist:
            return FilterResult(
                'Blacklist',
                FilterStatus.FAIL,
                reason=f"{symbol} is blacklisted"
            )
        
        return FilterResult('Blacklist', FilterStatus.PASS)
    
    def _check_whitelist(self, symbol: str) -> FilterResult:
        """CORE: Check if whitelist is enabled and symbol is in it."""
        if not self._whitelist:
            return FilterResult('Whitelist', FilterStatus.PASS)
        
        if symbol not in self._whitelist:
            return FilterResult(
                'Whitelist',
                FilterStatus.FAIL,
                reason=f"{symbol} not in whitelist"
            )
        
        return FilterResult('Whitelist', FilterStatus.PASS)
    
    def _check_rsi_extremes(self, ind: IndicatorValues, direction: str) -> FilterResult:
        """CORE: Reject if RSI in extreme zone."""
        rsi = ind.rsi
        
        # LONG: reject if overbought
        if direction == 'LONG' and rsi > self.rsi_overbought:
            return FilterResult(
                'RSI Overbought',
                FilterStatus.FAIL,
                reason=f"Overbought: RSI={rsi:.1f} > {self.rsi_overbought}",
                value=rsi,
                threshold=self.rsi_overbought
            )
        
        # SHORT: reject if oversold
        if direction == 'SHORT' and rsi < self.rsi_oversold:
            return FilterResult(
                'RSI Oversold',
                FilterStatus.FAIL,
                reason=f"Oversold: RSI={rsi:.1f} < {self.rsi_oversold}",
                value=rsi,
                threshold=self.rsi_oversold
            )
        
        return FilterResult('RSI Extremes', FilterStatus.PASS, value=rsi)
    
    def _check_min_volatility(self, ind: IndicatorValues) -> FilterResult:
        """CORE: Reject if ATR too low."""
        atr_pct = ind.atr_pct
        
        if atr_pct < self.min_atr_pct:
            return FilterResult(
                'Min Volatility',
                FilterStatus.FAIL,
                reason=f"ATR too low: {atr_pct:.2f}% < {self.min_atr_pct}%",
                value=atr_pct,
                threshold=self.min_atr_pct
            )
        
        return FilterResult('Min Volatility', FilterStatus.PASS, value=atr_pct)
    
    def _check_min_volume(self, ind: IndicatorValues) -> FilterResult:
        """CORE: Reject if volume too low."""
        vol_ratio = ind.volume_ratio
        
        if vol_ratio < self.min_volume_ratio:
            return FilterResult(
                'Min Volume',
                FilterStatus.FAIL,
                reason=f"Volume too low: {vol_ratio:.2f}x < {self.min_volume_ratio}x",
                value=vol_ratio,
                threshold=self.min_volume_ratio
            )
        
        return FilterResult('Min Volume', FilterStatus.PASS, value=vol_ratio)
    
    def _check_max_spread(self, spread_pct: float) -> FilterResult:
        """CORE: Reject if spread too wide."""
        if spread_pct > self.max_spread_pct:
            return FilterResult(
                'Max Spread',
                FilterStatus.FAIL,
                reason=f"Spread too wide: {spread_pct:.3f}% > {self.max_spread_pct}%",
                value=spread_pct,
                threshold=self.max_spread_pct
            )
        
        return FilterResult('Max Spread', FilterStatus.PASS, value=spread_pct)
    
    def _check_cooldown(self, symbol: str) -> FilterResult:
        """CORE: Check signal cooldown period."""
        if symbol in self._signal_history:
            last_signal = self._signal_history[symbol]
            cooldown_delta = timedelta(minutes=self.signal_cooldown_min)
            
            if datetime.now() - last_signal < cooldown_delta:
                remaining = (last_signal + cooldown_delta - datetime.now()).seconds // 60
                return FilterResult(
                    'Cooldown',
                    FilterStatus.FAIL,
                    reason=f"Cooldown active: {remaining}min remaining"
                )
        
        # Update history
        self._signal_history[symbol] = datetime.now()
        
        return FilterResult('Cooldown', FilterStatus.PASS)
    
    def _check_max_positions(self, current_positions: int) -> FilterResult:
        """CORE: Check max open positions limit."""
        if current_positions >= self.max_positions:
            return FilterResult(
                'Max Positions',
                FilterStatus.FAIL,
                reason=f"Max positions reached: {current_positions}/{self.max_positions}",
                value=float(current_positions),
                threshold=float(self.max_positions)
            )
        
        return FilterResult('Max Positions', FilterStatus.PASS, value=float(current_positions))
    
    # =========================================================================
    # OPTIONAL FILTERS (WARNINGS ONLY)
    # =========================================================================
    
    def _check_weak_trend(self, ind: IndicatorValues) -> FilterResult:
        """OPTIONAL: Warn if ADX low (weak trend)."""
        adx = ind.adx
        
        if adx < self.warn_adx_threshold:
            return FilterResult(
                'Weak Trend',
                FilterStatus.WARNING,
                reason=f"ADX low: {adx:.1f} < {self.warn_adx_threshold}",
                value=adx,
                threshold=self.warn_adx_threshold
            )
        
        return FilterResult('Weak Trend', FilterStatus.PASS, value=adx)
    
    def _check_ema_distance(self, ind: IndicatorValues, direction: str) -> FilterResult:
        """OPTIONAL: Warn if price too far from EMAs."""
        price = ind.close
        ema9 = ind.ema9
        
        if ema9 == 0:
            return FilterResult('EMA Distance', FilterStatus.PASS)
        
        distance_pct = abs((price - ema9) / ema9) * 100
        
        if distance_pct > self.warn_ema_distance_pct:
            return FilterResult(
                'EMA Distance',
                FilterStatus.WARNING,
                reason=f"Price far from EMA9: {distance_pct:.2f}% > {self.warn_ema_distance_pct}%",
                value=distance_pct,
                threshold=self.warn_ema_distance_pct
            )
        
        return FilterResult('EMA Distance', FilterStatus.PASS, value=distance_pct)
    
    def _check_high_volatility(self, ind: IndicatorValues) -> FilterResult:
        """OPTIONAL: Warn if extremely high volatility."""
        atr_pct = ind.atr_pct
        
        if atr_pct > self.warn_high_volatility_pct:
            return FilterResult(
                'High Volatility',
                FilterStatus.WARNING,
                reason=f"Extreme volatility: ATR {atr_pct:.2f}% > {self.warn_high_volatility_pct}%",
                value=atr_pct,
                threshold=self.warn_high_volatility_pct
            )
        
        return FilterResult('High Volatility', FilterStatus.PASS, value=atr_pct)
    
    def _check_volume_spike(self, ind: IndicatorValues) -> FilterResult:
        """OPTIONAL: Warn if unusual volume spike."""
        vol_ratio = ind.volume_ratio
        
        if vol_ratio > self.warn_volume_spike_ratio:
            return FilterResult(
                'Volume Spike',
                FilterStatus.WARNING,
                reason=f"Volume spike: {vol_ratio:.1f}x > {self.warn_volume_spike_ratio}x",
                value=vol_ratio,
                threshold=self.warn_volume_spike_ratio
            )
        
        return FilterResult('Volume Spike', FilterStatus.PASS, value=vol_ratio)
    
    def _check_scalp_spread(self, spread_pct: float) -> FilterResult:
        """OPTIONAL: Alert on ultra tight spread (scalp opportunity)."""
        if spread_pct < self.scalp_spread_pct:
            return FilterResult(
                'Scalp Opportunity',
                FilterStatus.WARNING,
                reason=f"Ultra tight spread: {spread_pct:.3f}% < {self.scalp_spread_pct}%",
                value=spread_pct,
                threshold=self.scalp_spread_pct
            )
        
        return FilterResult('Scalp Opportunity', FilterStatus.PASS, value=spread_pct)
    
    # =========================================================================
    # MANAGEMENT
    # =========================================================================
    
    def add_to_blacklist(self, symbol: str):
        """Add symbol to blacklist."""
        self._blacklist.add(symbol)
    
    def remove_from_blacklist(self, symbol: str):
        """Remove symbol from blacklist."""
        self._blacklist.discard(symbol)
    
    def add_to_whitelist(self, symbol: str):
        """Add symbol to whitelist."""
        self._whitelist.add(symbol)
    
    def remove_from_whitelist(self, symbol: str):
        """Remove symbol from whitelist."""
        self._whitelist.discard(symbol)
    
    def reset_cooldown(self, symbol: str):
        """Reset cooldown for symbol."""
        if symbol in self._signal_history:
            del self._signal_history[symbol]
    
    def clear_all_cooldowns(self):
        """Clear all cooldowns."""
        self._signal_history.clear()
    
    def get_cooldown_remaining(self, symbol: str) -> int:
        """Get remaining cooldown time in minutes."""
        if symbol not in self._signal_history:
            return 0
        
        last_signal = self._signal_history[symbol]
        cooldown_delta = timedelta(minutes=self.signal_cooldown_min)
        remaining = (last_signal + cooldown_delta - datetime.now()).seconds // 60
        
        return max(0, remaining)


# =============================================================================
# CONVENIENCE FUNCTION
# =============================================================================

def create_filter(config) -> SignalFilter:
    """Create filter instance."""
    return SignalFilter(config)