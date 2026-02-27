"""
Risk management system.

Features:
- Position sizing (fixed USD, % of wallet, Kelly criterion)
- Stop-loss management (hard SL, breakeven, trailing)
- Take-profit logic (trailing TP)
- Max drawdown protection (circuit breaker)
- Portfolio risk limits
- Emergency kill switch

All configurable via config.yaml
"""

from dataclasses import dataclass
from typing import Optional, Dict, List
from datetime import datetime, timedelta
from enum import Enum
import math

from signalbolt.core.indicators import IndicatorValues


# =============================================================================
# POSITION SIZING MODES
# =============================================================================

class PositionSizingMode(Enum):
    """Position sizing calculation mode."""
    FIXED_USD = "fixed_usd"           # Fixed dollar amount
    WALLET_PCT = "wallet_pct"         # Percentage of wallet
    RISK_PCT = "risk_pct"             # % of wallet to risk (based on SL)
    KELLY = "kelly"                   # Kelly Criterion (advanced)


# =============================================================================
# STOP-LOSS MODES
# =============================================================================

class StopLossMode(Enum):
    """Stop-loss type."""
    HARD = "hard"                     # Fixed % from entry
    ATR_BASED = "atr_based"           # Based on ATR
    EMA_BASED = "ema_based"           # Below EMA
    TRAILING = "trailing"             # Trailing stop


# =============================================================================
# POSITION INFO
# =============================================================================

@dataclass
class PositionSize:
    """Calculated position size."""
    
    usdt_amount: float                # USDT to invest
    coin_quantity: float              # Coin quantity to buy
    entry_price: float                # Expected entry price
    stop_loss_price: float            # Stop-loss price
    stop_loss_pct: float              # SL % from entry
    risk_usdt: float                  # USDT at risk (if SL hit)
    risk_pct: float                   # % of wallet at risk
    
    def to_dict(self) -> dict:
        """Convert to dict."""
        return {
            'usdt_amount': round(self.usdt_amount, 2),
            'coin_quantity': round(self.coin_quantity, 8),
            'entry_price': round(self.entry_price, 8),
            'stop_loss_price': round(self.stop_loss_price, 8),
            'stop_loss_pct': round(self.stop_loss_pct, 2),
            'risk_usdt': round(self.risk_usdt, 2),
            'risk_pct': round(self.risk_pct, 2),
        }


@dataclass
class StopLossUpdate:
    """Stop-loss update information."""
    
    old_price: float
    new_price: float
    reason: str
    timestamp: datetime
    
    def __repr__(self) -> str:
        return f"SL: {self.old_price:.8f} → {self.new_price:.8f} ({self.reason})"


# =============================================================================
# RISK MANAGER
# =============================================================================

class RiskManager:
    """
    Risk management system.
    
    Usage:
        risk = RiskManager(config)
        
        # Calculate position size
        size = risk.calculate_position_size(
            symbol='BTCUSDT',
            direction='LONG',
            entry_price=67234.50,
            wallet_balance=1000.0,
            indicators=ind
        )
        
        # Check if trade allowed
        if risk.can_open_position(current_positions=1):
            # Open trade
        
        # Update stop-loss (in trading loop)
        new_sl = risk.update_stop_loss(
            position=position,
            current_price=67450.0,
            indicators=ind
        )
    """
    
    def __init__(self, config):
        """
        Initialize risk manager.
        
        Args:
            config: Config instance
        """
        self.config = config
        
        # Load settings
        self._load_settings()
        
        # State
        self._emergency_stop = False
        self._max_drawdown_hit = False
        self._starting_balance: Optional[float] = None
        self._peak_balance: Optional[float] = None
        
        # Statistics (for Kelly)
        self._trade_history: List[float] = []  # PnL %
        self._win_rate: float = 0.5
        self._avg_win: float = 1.0
        self._avg_loss: float = 1.0
    
    def _load_settings(self):
        """Load risk settings from config."""
        # Position sizing
        self.sizing_mode = PositionSizingMode(
            self.config.get('risk', 'sizing_mode', default='wallet_pct')
        )
        self.fixed_usd = self.config.get('risk', 'fixed_usd', default=100.0)
        self.wallet_pct = self.config.get('spot', 'wallet_pct', default=50.0)
        self.risk_pct_per_trade = self.config.get('risk', 'risk_pct_per_trade', default=2.0)
        self.kelly_fraction = self.config.get('risk', 'kelly_fraction', default=0.25)
        
        # Stop-loss
        self.sl_mode = StopLossMode(
            self.config.get('risk', 'sl_mode', default='hard')
        )
        self.hard_sl_pct = abs(self.config.get('spot', 'hard_sl_pct', default=-2.0))
        self.atr_multiplier = self.config.get('risk', 'atr_multiplier', default=1.5)
        self.ema_distance_pct = self.config.get('risk', 'ema_distance_pct', default=0.5)
        
        # Breakeven & Trailing
        self.be_activation_pct = self.config.get('spot', 'be_activation_pct', default=0.5)
        self.trail_distance_pct = self.config.get('spot', 'trail_distance_pct', default=0.4)
        
        # Portfolio limits
        self.max_positions = self.config.get('spot', 'max_positions', default=1)
        self.max_portfolio_risk_pct = self.config.get('risk', 'max_portfolio_risk_pct', default=10.0)
        self.max_drawdown_pct = self.config.get('risk', 'max_drawdown_pct', default=20.0)
        
        # Emergency stop
        self.enable_circuit_breaker = self.config.get('risk', 'enable_circuit_breaker', default=True)
        self.max_consecutive_losses = self.config.get('risk', 'max_consecutive_losses', default=3)
    
    # =========================================================================
    # POSITION SIZING
    # =========================================================================
    
    def calculate_position_size(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        wallet_balance: float,
        indicators: IndicatorValues,
        current_positions: int = 0
    ) -> PositionSize:
        """
        Calculate position size based on risk settings.
        
        Args:
            symbol: Trading symbol
            direction: 'LONG' or 'SHORT'
            entry_price: Expected entry price
            wallet_balance: Current wallet balance (USDT)
            indicators: Indicator values
            current_positions: Number of open positions
        
        Returns:
            PositionSize instance
        """
        # Calculate stop-loss price
        sl_price = self._calculate_stop_loss_price(
            entry_price=entry_price,
            direction=direction,
            indicators=indicators
        )
        
        sl_pct = abs((sl_price - entry_price) / entry_price * 100)
        
        # Calculate USDT amount based on sizing mode
        if self.sizing_mode == PositionSizingMode.FIXED_USD:
            usdt_amount = self.fixed_usd
        
        elif self.sizing_mode == PositionSizingMode.WALLET_PCT:
            usdt_amount = wallet_balance * (self.wallet_pct / 100)
        
        elif self.sizing_mode == PositionSizingMode.RISK_PCT:
            # Risk-based: invest amount where SL hit = X% of wallet
            risk_usdt = wallet_balance * (self.risk_pct_per_trade / 100)
            usdt_amount = risk_usdt / (sl_pct / 100)
        
        elif self.sizing_mode == PositionSizingMode.KELLY:
            # Kelly Criterion: f = (bp - q) / b
            kelly_pct = self._calculate_kelly_fraction()
            usdt_amount = wallet_balance * kelly_pct
        
        else:
            # Fallback
            usdt_amount = wallet_balance * (self.wallet_pct / 100)
        
        # Apply position limit (if multiple positions allowed)
        if self.max_positions > 1:
            usdt_amount = min(usdt_amount, wallet_balance / self.max_positions)
        
        # Ensure minimum size (e.g., $10)
        usdt_amount = max(usdt_amount, 10.0)
        
        # Ensure maximum size (don't exceed wallet)
        usdt_amount = min(usdt_amount, wallet_balance * 0.95)  # Keep 5% buffer
        
        # Calculate coin quantity
        coin_quantity = usdt_amount / entry_price
        
        # Calculate actual risk
        risk_usdt = usdt_amount * (sl_pct / 100)
        risk_pct = (risk_usdt / wallet_balance) * 100
        
        return PositionSize(
            usdt_amount=usdt_amount,
            coin_quantity=coin_quantity,
            entry_price=entry_price,
            stop_loss_price=sl_price,
            stop_loss_pct=sl_pct,
            risk_usdt=risk_usdt,
            risk_pct=risk_pct
        )
    
    def _calculate_stop_loss_price(
        self,
        entry_price: float,
        direction: str,
        indicators: IndicatorValues
    ) -> float:
        """Calculate initial stop-loss price."""
        
        if self.sl_mode == StopLossMode.HARD:
            # Fixed percentage
            if direction == 'LONG':
                sl_price = entry_price * (1 - self.hard_sl_pct / 100)
            else:
                sl_price = entry_price * (1 + self.hard_sl_pct / 100)
        
        elif self.sl_mode == StopLossMode.ATR_BASED:
            # ATR-based
            atr_distance = indicators.atr * self.atr_multiplier
            if direction == 'LONG':
                sl_price = entry_price - atr_distance
            else:
                sl_price = entry_price + atr_distance
        
        elif self.sl_mode == StopLossMode.EMA_BASED:
            # Below EMA21
            ema = indicators.ema21
            distance = ema * (self.ema_distance_pct / 100)
            
            if direction == 'LONG':
                sl_price = ema - distance
            else:
                sl_price = ema + distance
        
        else:
            # Fallback to hard SL
            if direction == 'LONG':
                sl_price = entry_price * (1 - self.hard_sl_pct / 100)
            else:
                sl_price = entry_price * (1 + self.hard_sl_pct / 100)
        
        return sl_price
    
    def _calculate_kelly_fraction(self) -> float:
        """
        Calculate Kelly Criterion fraction.
        
        Formula: f = (bp - q) / b
        where:
            b = avg_win / avg_loss
            p = win_rate
            q = 1 - win_rate
        """
        if self._avg_loss == 0:
            return self.wallet_pct / 100
        
        b = self._avg_win / self._avg_loss
        p = self._win_rate
        q = 1 - p
        
        kelly = (b * p - q) / b
        
        # Apply Kelly fraction (usually 0.25 = quarter Kelly)
        kelly = max(0, kelly) * self.kelly_fraction
        
        # Cap at wallet_pct
        kelly = min(kelly, self.wallet_pct / 100)
        
        return kelly
    
    # =========================================================================
    # STOP-LOSS MANAGEMENT
    # =========================================================================
    
    def update_stop_loss(
        self,
        entry_price: float,
        current_sl: float,
        current_price: float,
        highest_price: float,
        direction: str,
        indicators: IndicatorValues,
        is_breakeven_active: bool = False
    ) -> Optional[StopLossUpdate]:
        """
        Update stop-loss (breakeven + trailing).
        
        Args:
            entry_price: Entry price
            current_sl: Current stop-loss price
            current_price: Current market price
            highest_price: Highest price since entry
            direction: 'LONG' or 'SHORT'
            indicators: Current indicators
            is_breakeven_active: If breakeven already activated
        
        Returns:
            StopLossUpdate if SL should be updated, None otherwise
        """
        profit_pct = ((current_price - entry_price) / entry_price) * 100
        if direction == 'SHORT':
            profit_pct = -profit_pct
        
        # =====================================================================
        # STEP 1: Breakeven activation
        # =====================================================================
        
        if not is_breakeven_active and profit_pct >= self.be_activation_pct:
            # Move SL to breakeven (entry + fees)
            # Assume 0.1% total fees (entry + exit)
            fee_pct = 0.1
            
            if direction == 'LONG':
                new_sl = entry_price * (1 + fee_pct / 100)
            else:
                new_sl = entry_price * (1 - fee_pct / 100)
            
            if direction == 'LONG' and new_sl > current_sl:
                return StopLossUpdate(
                    old_price=current_sl,
                    new_price=new_sl,
                    reason=f"Breakeven activated (profit: {profit_pct:.2f}%)",
                    timestamp=datetime.now()
                )
            elif direction == 'SHORT' and new_sl < current_sl:
                return StopLossUpdate(
                    old_price=current_sl,
                    new_price=new_sl,
                    reason=f"Breakeven activated (profit: {profit_pct:.2f}%)",
                    timestamp=datetime.now()
                )
        
        # =====================================================================
        # STEP 2: Trailing stop
        # =====================================================================
        
        if is_breakeven_active:
            # Trail based on highest price
            trail_distance = highest_price * (self.trail_distance_pct / 100)
            
            if direction == 'LONG':
                new_sl = highest_price - trail_distance
                
                if new_sl > current_sl:
                    return StopLossUpdate(
                        old_price=current_sl,
                        new_price=new_sl,
                        reason=f"Trailing stop (highest: {highest_price:.8f})",
                        timestamp=datetime.now()
                    )
            
            else:  # SHORT
                new_sl = highest_price + trail_distance
                
                if new_sl < current_sl:
                    return StopLossUpdate(
                        old_price=current_sl,
                        new_price=new_sl,
                        reason=f"Trailing stop (lowest: {highest_price:.8f})",
                        timestamp=datetime.now()
                    )
        
        return None
    
    def should_close_by_stop_loss(
        self,
        current_price: float,
        stop_loss_price: float,
        direction: str
    ) -> bool:
        """Check if stop-loss should be triggered."""
        if direction == 'LONG':
            return current_price <= stop_loss_price
        else:
            return current_price >= stop_loss_price
    
    # =========================================================================
    # PORTFOLIO RISK CHECKS
    # =========================================================================
    
    def can_open_position(
        self,
        current_positions: int,
        total_risk_pct: float = 0.0
    ) -> tuple[bool, Optional[str]]:
        """
        Check if new position can be opened.
        
        Args:
            current_positions: Number of currently open positions
            total_risk_pct: Total portfolio risk % (sum of all position risks)
        
        Returns:
            (can_open, reason_if_not)
        """
        # Check emergency stop
        if self._emergency_stop:
            return False, "Emergency stop active"
        
        # Check max drawdown
        if self._max_drawdown_hit:
            return False, "Max drawdown limit reached"
        
        # Check max positions
        if current_positions >= self.max_positions:
            return False, f"Max positions reached ({current_positions}/{self.max_positions})"
        
        # Check portfolio risk
        if total_risk_pct >= self.max_portfolio_risk_pct:
            return False, f"Portfolio risk too high ({total_risk_pct:.1f}%/{self.max_portfolio_risk_pct}%)"
        
        return True, None
    
    def check_drawdown(
        self,
        current_balance: float,
        starting_balance: Optional[float] = None
    ) -> tuple[bool, float]:
        """
        Check if max drawdown exceeded.
        
        Args:
            current_balance: Current wallet balance
            starting_balance: Starting balance (optional, will use saved if None)
        
        Returns:
            (exceeded, current_drawdown_pct)
        """
        if starting_balance is not None:
            self._starting_balance = starting_balance
        
        if self._starting_balance is None:
            self._starting_balance = current_balance
        
        # Update peak
        if self._peak_balance is None or current_balance > self._peak_balance:
            self._peak_balance = current_balance
        
        # Calculate drawdown from peak
        drawdown_pct = ((self._peak_balance - current_balance) / self._peak_balance) * 100
        
        if drawdown_pct >= self.max_drawdown_pct:
            self._max_drawdown_hit = True
            return True, drawdown_pct
        
        return False, drawdown_pct
    
    # =========================================================================
    # EMERGENCY CONTROLS
    # =========================================================================
    
    def activate_emergency_stop(self):
        """Activate emergency stop (no new trades)."""
        self._emergency_stop = True
    
    def deactivate_emergency_stop(self):
        """Deactivate emergency stop."""
        self._emergency_stop = False
    
    def reset_drawdown_protection(self):
        """Reset drawdown protection."""
        self._max_drawdown_hit = False
        self._peak_balance = None
    
    @property
    def emergency_stop_active(self) -> bool:
        """Check if emergency stop is active."""
        return self._emergency_stop
    
    @property
    def max_drawdown_hit(self) -> bool:
        """Check if max drawdown was hit."""
        return self._max_drawdown_hit
    
    # =========================================================================
    # STATISTICS (for Kelly Criterion)
    # =========================================================================
    
    def record_trade(self, pnl_pct: float):
        """
        Record trade result for statistics.
        
        Args:
            pnl_pct: Trade PnL in % (positive = win, negative = loss)
        """
        self._trade_history.append(pnl_pct)
        
        # Keep only last 100 trades
        if len(self._trade_history) > 100:
            self._trade_history.pop(0)
        
        # Recalculate stats
        self._update_statistics()
    
    def _update_statistics(self):
        """Update win rate and avg win/loss."""
        if not self._trade_history:
            return
        
        wins = [t for t in self._trade_history if t > 0]
        losses = [t for t in self._trade_history if t < 0]
        
        # Win rate
        self._win_rate = len(wins) / len(self._trade_history) if self._trade_history else 0.5
        
        # Average win
        self._avg_win = sum(wins) / len(wins) if wins else 1.0
        
        # Average loss (absolute value)
        self._avg_loss = abs(sum(losses) / len(losses)) if losses else 1.0
    
    def get_statistics(self) -> dict:
        """Get risk statistics."""
        return {
            'emergency_stop': self._emergency_stop,
            'max_drawdown_hit': self._max_drawdown_hit,
            'trade_count': len(self._trade_history),
            'win_rate': round(self._win_rate * 100, 1),
            'avg_win_pct': round(self._avg_win, 2),
            'avg_loss_pct': round(self._avg_loss, 2),
            'kelly_fraction': round(self._calculate_kelly_fraction() * 100, 2),
        }


# =============================================================================
# CONVENIENCE FUNCTION
# =============================================================================

def create_risk_manager(config) -> RiskManager:
    """Create risk manager instance."""
    return RiskManager(config)