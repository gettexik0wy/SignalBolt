"""
Adaptive regime management.

Monitors regime changes and automatically switches configuration.
Logs transitions and provides regime history.
"""

from typing import Optional, List, Tuple
from datetime import datetime
from dataclasses import dataclass
import pandas as pd

from signalbolt.core.config import Config
from signalbolt.regime.detector import RegimeDetector, MarketRegime
from signalbolt.regime.presets import get_regime_preset, RegimePreset
from signalbolt.utils.logger import get_logger

log = get_logger('signalbolt.regime.adaptive')


@dataclass
class RegimeTransition:
    """Record of regime change."""
    
    timestamp: datetime
    from_regime: MarketRegime
    to_regime: MarketRegime
    btc_price: float
    btc_change_30d: float
    drawdown: float
    
    def __str__(self) -> str:
        return (f"{self.timestamp.strftime('%Y-%m-%d %H:%M')} | "
                f"{self.from_regime.value} → {self.to_regime.value} | "
                f"BTC: ${self.btc_price:,.0f} "
                f"({self.btc_change_30d:+.1f}%, DD: {self.drawdown:.1f}%)")


class AdaptiveRegimeManager:
    """
    Manages regime detection and automatic config switching.
    
    Features:
    - Continuous regime monitoring
    - Automatic preset switching
    - Transition logging
    - Regime history
    - Statistics
    
    Usage:
        manager = AdaptiveRegimeManager(config)
        
        # In main loop:
        regime_changed = manager.update(btc_df)
        if regime_changed:
            new_preset = manager.get_current_preset()
            # Apply new preset...
    """
    
    def __init__(self, config: Config):
        """
        Initialize manager.
        
        Args:
            config: Config instance
        """
        self.config = config
        self.detector = RegimeDetector()
        
        # State
        self.current_regime: Optional[MarketRegime] = None
        self.current_preset: Optional[RegimePreset] = None
        
        # History
        self.transitions: List[RegimeTransition] = []
        self.regime_durations: List[Tuple[MarketRegime, float]] = []  # (regime, minutes)
        
        # Stats
        self.last_update: Optional[datetime] = None
        self.regime_start_time: Optional[datetime] = None
        
        log.info("Adaptive Regime Manager initialized")
    
    # =========================================================================
    # PUBLIC API
    # =========================================================================
    
    def update(self, btc_df: pd.DataFrame) -> bool:
        """
        Update regime detection.
        
        Args:
            btc_df: BTC OHLCV DataFrame (for regime detection)
        
        Returns:
            True if regime changed, False otherwise
        
        Usage:
            if manager.update(btc_df):
                print("Regime changed!")
                new_preset = manager.get_current_preset()
        """
        
        # Detect current regime
        new_regime = self.detector.detect(btc_df)
        
        # First run
        if self.current_regime is None:
            self._initialize_regime(new_regime, btc_df)
            return False
        
        # Check if changed
        if new_regime != self.current_regime:
            self._handle_transition(new_regime, btc_df)
            return True
        
        # No change
        self.last_update = datetime.now()
        return False
    
    def get_current_regime(self) -> Optional[MarketRegime]:
        """Get current regime."""
        return self.current_regime
    
    def get_current_preset(self) -> Optional[RegimePreset]:
        """Get current preset."""
        return self.current_preset
    
    def get_regime_duration_minutes(self) -> float:
        """Get duration of current regime in minutes."""
        if self.regime_start_time is None:
            return 0.0
        
        return (datetime.now() - self.regime_start_time).total_seconds() / 60
    
    def get_transitions(self, limit: int = 10) -> List[RegimeTransition]:
        """Get recent transitions."""
        return self.transitions[-limit:]
    
    def get_regime_stats(self) -> dict:
        """
        Get regime statistics.
        
        Returns:
            Dict with:
            - current_regime
            - duration_minutes
            - total_transitions
            - regime_counts
            - avg_duration_per_regime
        """
        
        from collections import Counter
        
        # Count regimes
        regime_counts = Counter(self.regime_durations)
        
        # Average duration per regime
        avg_durations = {}
        for regime in MarketRegime:
            durations = [d for r, d in self.regime_durations if r == regime]
            avg_durations[regime.value] = sum(durations) / len(durations) if durations else 0
        
        return {
            'current_regime': self.current_regime.value if self.current_regime else None,
            'duration_minutes': self.get_regime_duration_minutes(),
            'total_transitions': len(self.transitions),
            'regime_counts': {r.value: c for r, c in regime_counts.items()},
            'avg_duration_per_regime': avg_durations,
        }
    
    def print_status(self):
        """Print current status."""
        
        if self.current_regime is None:
            print("❓ Regime: Not initialized")
            return
        
        duration = self.get_regime_duration_minutes()
        
        print(f"\n{'='*60}")
        print(f"📊 REGIME STATUS")
        print(f"{'='*60}")
        print(f"Current:  {self.current_regime.value}")
        print(f"Duration: {duration:.0f} minutes ({duration/60:.1f} hours)")
        print(f"Transitions: {len(self.transitions)}")
        
        if self.current_preset:
            print(f"\n📋 CURRENT PRESET:")
            print(f"  Stop-loss:    {self.current_preset.stop_loss_pct:.2f}%")
            print(f"  Breakeven:    {self.current_preset.breakeven_trigger_pct:.2f}%")
            print(f"  Trailing:     {self.current_preset.trailing_stop_pct:.2f}%")
            print(f"  Min score:    {self.current_preset.min_signal_score:.0f}")
            print(f"  Max positions: {self.current_preset.max_positions}")
        
        if self.transitions:
            print(f"\n🔄 RECENT TRANSITIONS:")
            for transition in self.transitions[-3:]:
                print(f"  {transition}")
        
        print(f"{'='*60}\n")
    
    # =========================================================================
    # INTERNAL
    # =========================================================================
    
    def _initialize_regime(self, regime: MarketRegime, btc_df: pd.DataFrame):
        """Initialize regime (first run)."""
        
        self.current_regime = regime
        self.current_preset = get_regime_preset(regime, self.config)
        self.regime_start_time = datetime.now()
        self.last_update = datetime.now()
        
        btc_price = btc_df['close'].iloc[-1]
        
        log.info(f"🎯 Initial regime: {regime.value} (BTC: ${btc_price:,.0f})")
    
    def _handle_transition(self, new_regime: MarketRegime, btc_df: pd.DataFrame):
        """Handle regime transition."""
        
        # Calculate duration of previous regime
        duration_min = self.get_regime_duration_minutes()
        self.regime_durations.append((self.current_regime, duration_min))
        
        # Get BTC stats
        btc_price = btc_df['close'].iloc[-1]
        change_30d = self.detector._calculate_btc_change(btc_df)
        drawdown = self.detector._calculate_drawdown(btc_df)
        
        # Create transition record
        transition = RegimeTransition(
            timestamp=datetime.now(),
            from_regime=self.current_regime,
            to_regime=new_regime,
            btc_price=btc_price,
            btc_change_30d=change_30d,
            drawdown=drawdown
        )
        
        self.transitions.append(transition)
        
        # Update state
        self.current_regime = new_regime
        self.current_preset = get_regime_preset(new_regime, self.config)
        self.regime_start_time = datetime.now()
        self.last_update = datetime.now()
        
        # Log
        log.info(f"🔄 REGIME CHANGE: {transition}")
        log.info(f"   New preset: SL={self.current_preset.stop_loss_pct:.2f}%, "
                f"MinScore={self.current_preset.min_signal_score:.0f}")
    
    def __repr__(self) -> str:
        if self.current_regime:
            return f"AdaptiveRegimeManager(regime={self.current_regime.value})"
        return "AdaptiveRegimeManager(not initialized)"