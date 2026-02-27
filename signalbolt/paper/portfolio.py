"""
Virtual portfolio for paper trading.

Features:
- Virtual balances (USDT + assets)
- Position tracking with full state
- P&L calculation (realized + unrealized)
- Trade history
- SL/TP/Trailing management
- Fee simulation

Usage:
    portfolio = PaperPortfolio(initial_balance=1000.0)
    
    # Open position
    position = portfolio.open_position(
        symbol='BTCUSDT',
        direction='LONG',
        quantity=0.001,
        entry_price=67234.50,
        stop_loss_pct=-2.0
    )
    
    # Update with current price
    portfolio.update_prices({'BTCUSDT': 67500.0})
    
    # Close position
    result = portfolio.close_position(position.id, exit_price=67500.0, reason='TAKE_PROFIT')
    
    # Get stats
    print(portfolio.get_stats())
"""

import uuid
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum
from copy import deepcopy
import json

from signalbolt.utils.logger import get_logger

log = get_logger('signalbolt.paper.portfolio')


# =============================================================================
# ENUMS
# =============================================================================

class PositionStatus(Enum):
    """Position status."""
    OPEN = "open"
    CLOSED = "closed"
    PENDING = "pending"  # For limit orders


class PositionSide(Enum):
    """Position side."""
    LONG = "LONG"
    SHORT = "SHORT"


class CloseReason(Enum):
    """Position close reason."""
    HARD_SL = "HARD_SL"
    TRAILING_SL = "TRAILING_SL"
    TAKE_PROFIT = "TAKE_PROFIT"
    TIMEOUT = "TIMEOUT"
    MANUAL = "MANUAL"
    SIGNAL_EXIT = "SIGNAL_EXIT"
    EMERGENCY = "EMERGENCY"


# =============================================================================
# POSITION
# =============================================================================

@dataclass
class Position:
    """Trading position."""
    
    # Identification
    id: str
    symbol: str
    direction: str  # 'LONG' or 'SHORT'
    
    # Entry
    entry_time: datetime
    entry_price: float
    quantity: float
    size_usd: float
    
    # Status
    status: PositionStatus = PositionStatus.OPEN
    
    # Exit (filled when closed)
    exit_time: Optional[datetime] = None
    exit_price: float = 0.0
    close_reason: Optional[CloseReason] = None
    
    # Risk management
    stop_loss_price: float = 0.0
    stop_loss_pct: float = -2.0
    take_profit_price: Optional[float] = None
    take_profit_pct: Optional[float] = None
    
    # Trailing stop
    trailing_active: bool = False
    trailing_activation_pct: float = 0.5
    trailing_distance_pct: float = 0.4
    current_trail_price: float = 0.0
    
    # Price tracking
    highest_price: float = 0.0
    lowest_price: float = float('inf')
    highest_pnl_pct: float = 0.0
    lowest_pnl_pct: float = 0.0
    current_price: float = 0.0
    
    # Fees
    entry_fee_usd: float = 0.0
    exit_fee_usd: float = 0.0
    
    # Metadata
    signal_score: float = 0.0
    signal_regime: str = "unknown"
    notes: str = ""
    
    # Timestamps
    last_update: datetime = field(default_factory=datetime.now)
    breakeven_activated_at: Optional[datetime] = None
    
    @property
    def is_open(self) -> bool:
        return self.status == PositionStatus.OPEN
    
    @property
    def is_closed(self) -> bool:
        return self.status == PositionStatus.CLOSED
    
    @property
    def unrealized_pnl_pct(self) -> float:
        """Current unrealized P&L %."""
        if self.entry_price == 0:
            return 0.0
        
        if self.direction == 'LONG':
            return ((self.current_price - self.entry_price) / self.entry_price) * 100
        else:
            return ((self.entry_price - self.current_price) / self.entry_price) * 100
    
    @property
    def unrealized_pnl_usd(self) -> float:
        """Current unrealized P&L USD."""
        return self.size_usd * (self.unrealized_pnl_pct / 100)
    
    @property
    def realized_pnl_pct(self) -> float:
        """Realized P&L % (after close)."""
        if not self.is_closed or self.entry_price == 0:
            return 0.0
        
        if self.direction == 'LONG':
            return ((self.exit_price - self.entry_price) / self.entry_price) * 100
        else:
            return ((self.entry_price - self.exit_price) / self.entry_price) * 100
    
    @property
    def realized_pnl_usd(self) -> float:
        """Realized P&L USD (gross, before fees)."""
        return self.size_usd * (self.realized_pnl_pct / 100)
    
    @property
    def net_pnl_usd(self) -> float:
        """Net P&L USD (after fees)."""
        if self.is_closed:
            return self.realized_pnl_usd - self.entry_fee_usd - self.exit_fee_usd
        else:
            return self.unrealized_pnl_usd - self.entry_fee_usd
    
    @property
    def total_fees_usd(self) -> float:
        """Total fees paid."""
        return self.entry_fee_usd + self.exit_fee_usd
    
    @property
    def hold_time(self) -> timedelta:
        """Position hold time."""
        end = self.exit_time if self.is_closed else datetime.now()
        return end - self.entry_time
    
    @property
    def hold_time_minutes(self) -> float:
        """Hold time in minutes."""
        return self.hold_time.total_seconds() / 60
    
    def update_price(self, price: float):
        """Update current price and track highs/lows."""
        self.current_price = price
        self.last_update = datetime.now()
        
        # Track highs/lows
        if price > self.highest_price:
            self.highest_price = price
        
        if price < self.lowest_price:
            self.lowest_price = price
        
        # Track P&L extremes
        current_pnl = self.unrealized_pnl_pct
        
        if current_pnl > self.highest_pnl_pct:
            self.highest_pnl_pct = current_pnl
        
        if current_pnl < self.lowest_pnl_pct:
            self.lowest_pnl_pct = current_pnl
    
    def to_dict(self) -> dict:
        """Convert to dict."""
        return {
            'id': self.id,
            'symbol': self.symbol,
            'direction': self.direction,
            'status': self.status.value,
            'entry_time': self.entry_time.isoformat(),
            'entry_price': self.entry_price,
            'quantity': self.quantity,
            'size_usd': round(self.size_usd, 2),
            'exit_time': self.exit_time.isoformat() if self.exit_time else None,
            'exit_price': self.exit_price,
            'close_reason': self.close_reason.value if self.close_reason else None,
            'stop_loss_price': self.stop_loss_price,
            'stop_loss_pct': self.stop_loss_pct,
            'trailing_active': self.trailing_active,
            'current_price': self.current_price,
            'unrealized_pnl_pct': round(self.unrealized_pnl_pct, 2),
            'unrealized_pnl_usd': round(self.unrealized_pnl_usd, 2),
            'realized_pnl_pct': round(self.realized_pnl_pct, 2),
            'realized_pnl_usd': round(self.realized_pnl_usd, 2),
            'net_pnl_usd': round(self.net_pnl_usd, 2),
            'total_fees_usd': round(self.total_fees_usd, 2),
            'highest_pnl_pct': round(self.highest_pnl_pct, 2),
            'hold_time_minutes': round(self.hold_time_minutes, 1),
            'signal_score': self.signal_score,
            'signal_regime': self.signal_regime,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Position':
        """Create from dict."""
        data = dict(data)
        
        data['entry_time'] = datetime.fromisoformat(data['entry_time'])
        data['status'] = PositionStatus(data['status'])
        
        if data.get('exit_time'):
            data['exit_time'] = datetime.fromisoformat(data['exit_time'])
        
        if data.get('close_reason'):
            data['close_reason'] = CloseReason(data['close_reason'])
        
        # Remove computed fields
        for key in ['unrealized_pnl_pct', 'unrealized_pnl_usd', 'realized_pnl_pct',
                    'realized_pnl_usd', 'net_pnl_usd', 'total_fees_usd', 
                    'highest_pnl_pct', 'hold_time_minutes']:
            data.pop(key, None)
        
        return cls(**data)


# =============================================================================
# TRADE RESULT
# =============================================================================

@dataclass
class TradeResult:
    """Result of a closed trade."""
    
    position_id: str
    symbol: str
    direction: str
    
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    
    quantity: float
    size_usd: float
    
    pnl_pct: float
    pnl_usd: float
    net_pnl_usd: float
    fees_usd: float
    
    close_reason: CloseReason
    hold_time_minutes: float
    
    highest_pnl_pct: float
    signal_score: float
    signal_regime: str
    
    @property
    def is_win(self) -> bool:
        return self.net_pnl_usd > 0
    
    @property
    def is_loss(self) -> bool:
        return self.net_pnl_usd < 0
    
    def to_dict(self) -> dict:
        return {
            'position_id': self.position_id,
            'symbol': self.symbol,
            'direction': self.direction,
            'entry_time': self.entry_time.isoformat(),
            'entry_price': self.entry_price,
            'exit_time': self.exit_time.isoformat(),
            'exit_price': self.exit_price,
            'quantity': self.quantity,
            'size_usd': round(self.size_usd, 2),
            'pnl_pct': round(self.pnl_pct, 2),
            'pnl_usd': round(self.pnl_usd, 2),
            'net_pnl_usd': round(self.net_pnl_usd, 2),
            'fees_usd': round(self.fees_usd, 2),
            'close_reason': self.close_reason.value,
            'hold_time_minutes': round(self.hold_time_minutes, 1),
            'highest_pnl_pct': round(self.highest_pnl_pct, 2),
            'signal_score': self.signal_score,
            'signal_regime': self.signal_regime,
            'is_win': self.is_win,
        }


# =============================================================================
# PAPER PORTFOLIO
# =============================================================================

class PaperPortfolio:
    """
    Virtual portfolio for paper trading.
    
    Simulates:
    - Wallet balances (USDT + held assets)
    - Position management
    - Fee deduction
    - P&L tracking
    """
    
    def __init__(
        self,
        initial_balance: float = 1000.0,
        quote_asset: str = 'USDT',
        taker_fee_pct: float = 0.04,
        maker_fee_pct: float = 0.0,
        max_positions: int = 1
    ):
        """
        Initialize portfolio.
        
        Args:
            initial_balance: Starting balance in quote asset
            quote_asset: Quote asset (default USDT)
            taker_fee_pct: Taker fee % (default 0.04%)
            maker_fee_pct: Maker fee % (default 0%)
            max_positions: Maximum open positions
        """
        self.quote_asset = quote_asset
        self.taker_fee_pct = taker_fee_pct
        self.maker_fee_pct = maker_fee_pct
        self.max_positions = max_positions
        
        # Balances
        self._initial_balance = initial_balance
        self._quote_balance = initial_balance  # Free USDT
        self._asset_balances: Dict[str, float] = {}  # Held assets
        
        # Positions
        self._positions: Dict[str, Position] = {}  # Open positions by ID
        self._closed_positions: List[Position] = []
        
        # Trade history
        self._trade_results: List[TradeResult] = []
        
        # Tracking
        self._peak_balance = initial_balance
        self._lowest_balance = initial_balance
        self._total_fees_paid = 0.0
        
        # Timestamps
        self._created_at = datetime.now()
        self._last_update = datetime.now()
        
        log.info(f"PaperPortfolio initialized: ${initial_balance:.2f} {quote_asset}")
    
    # =========================================================================
    # BALANCE MANAGEMENT
    # =========================================================================
    
    @property
    def quote_balance(self) -> float:
        """Free quote asset balance."""
        return self._quote_balance
    
    @property
    def total_balance(self) -> float:
        """Total portfolio value in quote asset."""
        total = self._quote_balance
        
        for position in self._positions.values():
            if position.is_open:
                # Current value = size + unrealized P&L
                total += position.size_usd + position.unrealized_pnl_usd
        
        return total
    
    @property
    def unrealized_pnl(self) -> float:
        """Total unrealized P&L."""
        return sum(p.unrealized_pnl_usd for p in self._positions.values() if p.is_open)
    
    @property
    def realized_pnl(self) -> float:
        """Total realized P&L."""
        return sum(r.net_pnl_usd for r in self._trade_results)
    
    @property
    def total_pnl(self) -> float:
        """Total P&L (realized + unrealized)."""
        return self.total_balance - self._initial_balance
    
    @property
    def total_pnl_pct(self) -> float:
        """Total P&L %."""
        if self._initial_balance == 0:
            return 0.0
        return (self.total_pnl / self._initial_balance) * 100
    
    @property
    def drawdown_pct(self) -> float:
        """Current drawdown from peak."""
        if self._peak_balance == 0:
            return 0.0
        return ((self._peak_balance - self.total_balance) / self._peak_balance) * 100
    
    @property
    def max_drawdown_pct(self) -> float:
        """Maximum drawdown from peak."""
        if self._peak_balance == 0:
            return 0.0
        return ((self._peak_balance - self._lowest_balance) / self._peak_balance) * 100
    
    def get_asset_balance(self, asset: str) -> float:
        """Get balance of specific asset."""
        return self._asset_balances.get(asset, 0.0)
    
    # =========================================================================
    # POSITION MANAGEMENT
    # =========================================================================
    
    @property
    def open_positions(self) -> List[Position]:
        """Get all open positions."""
        return [p for p in self._positions.values() if p.is_open]
    
    @property
    def open_position_count(self) -> int:
        """Number of open positions."""
        return len(self.open_positions)
    
    @property
    def open_symbols(self) -> List[str]:
        """Symbols with open positions."""
        return [p.symbol for p in self.open_positions]
    
    def has_position(self, symbol: str) -> bool:
        """Check if there's an open position for symbol."""
        return symbol in self.open_symbols
    
    def get_position(self, position_id: str) -> Optional[Position]:
        """Get position by ID."""
        return self._positions.get(position_id)
    
    def get_position_by_symbol(self, symbol: str) -> Optional[Position]:
        """Get open position by symbol."""
        for position in self.open_positions:
            if position.symbol == symbol:
                return position
        return None
    
    def can_open_position(self) -> Tuple[bool, Optional[str]]:
        """Check if can open new position."""
        if self.open_position_count >= self.max_positions:
            return False, f"Max positions reached ({self.max_positions})"
        
        if self._quote_balance < 10:
            return False, "Insufficient balance"
        
        return True, None
    
    # =========================================================================
    # OPEN POSITION
    # =========================================================================
    
    def open_position(
        self,
        symbol: str,
        direction: str,
        quantity: float,
        entry_price: float,
        stop_loss_pct: float = -2.0,
        take_profit_pct: Optional[float] = None,
        trailing_activation_pct: float = 0.5,
        trailing_distance_pct: float = 0.4,
        signal_score: float = 0.0,
        signal_regime: str = "unknown",
        notes: str = ""
    ) -> Optional[Position]:
        """
        Open new position.
        
        Args:
            symbol: Trading symbol
            direction: 'LONG' or 'SHORT'
            quantity: Asset quantity
            entry_price: Entry price
            stop_loss_pct: Stop loss % (negative)
            take_profit_pct: Take profit %
            trailing_activation_pct: Trailing activation %
            trailing_distance_pct: Trailing distance %
            signal_score: Signal score
            signal_regime: Market regime
            notes: Additional notes
        
        Returns:
            Position or None if failed
        """
        # Validate
        can_open, reason = self.can_open_position()
        if not can_open:
            log.warning(f"Cannot open position: {reason}")
            return None
        
        if self.has_position(symbol):
            log.warning(f"Already have position in {symbol}")
            return None
        
        # Calculate size
        size_usd = quantity * entry_price
        
        if size_usd > self._quote_balance:
            log.warning(f"Insufficient balance: need ${size_usd:.2f}, have ${self._quote_balance:.2f}")
            return None
        
        # Calculate fee
        entry_fee = size_usd * (self.taker_fee_pct / 100)
        
        # Calculate SL/TP prices
        if direction == 'LONG':
            stop_loss_price = entry_price * (1 + stop_loss_pct / 100)
            take_profit_price = entry_price * (1 + take_profit_pct / 100) if take_profit_pct else None
        else:
            stop_loss_price = entry_price * (1 - stop_loss_pct / 100)
            take_profit_price = entry_price * (1 - take_profit_pct / 100) if take_profit_pct else None
        
        # Create position
        position_id = str(uuid.uuid4())[:8]
        
        position = Position(
            id=position_id,
            symbol=symbol,
            direction=direction,
            entry_time=datetime.now(),
            entry_price=entry_price,
            quantity=quantity,
            size_usd=size_usd,
            status=PositionStatus.OPEN,
            stop_loss_price=stop_loss_price,
            stop_loss_pct=stop_loss_pct,
            take_profit_price=take_profit_price,
            take_profit_pct=take_profit_pct,
            trailing_activation_pct=trailing_activation_pct,
            trailing_distance_pct=trailing_distance_pct,
            highest_price=entry_price,
            lowest_price=entry_price,
            current_price=entry_price,
            entry_fee_usd=entry_fee,
            signal_score=signal_score,
            signal_regime=signal_regime,
            notes=notes
        )
        
        # Update balances
        self._quote_balance -= (size_usd + entry_fee)
        self._total_fees_paid += entry_fee
        
        # Extract base asset
        base_asset = symbol.replace(self.quote_asset, '')
        self._asset_balances[base_asset] = self._asset_balances.get(base_asset, 0) + quantity
        
        # Store position
        self._positions[position_id] = position
        
        log.trade(f"OPEN {direction} {symbol}: {quantity:.8f} @ {entry_price:.8f} "
                 f"(size: ${size_usd:.2f}, SL: {stop_loss_pct}%)")
        
        return position
    
    # =========================================================================
    # CLOSE POSITION
    # =========================================================================
    
    def close_position(
        self,
        position_id: str,
        exit_price: float,
        reason: CloseReason = CloseReason.MANUAL
    ) -> Optional[TradeResult]:
        """
        Close position.
        
        Args:
            position_id: Position ID
            exit_price: Exit price
            reason: Close reason
        
        Returns:
            TradeResult or None
        """
        position = self._positions.get(position_id)
        
        if not position or not position.is_open:
            log.warning(f"Position not found or already closed: {position_id}")
            return None
        
        # Calculate exit fee
        exit_value = position.quantity * exit_price
        exit_fee = exit_value * (self.taker_fee_pct / 100)
        
        # Update position
        position.exit_time = datetime.now()
        position.exit_price = exit_price
        position.close_reason = reason
        position.status = PositionStatus.CLOSED
        position.exit_fee_usd = exit_fee
        position.current_price = exit_price
        
        # Calculate P&L
        pnl_pct = position.realized_pnl_pct
        pnl_usd = position.realized_pnl_usd
        net_pnl = position.net_pnl_usd
        
        # Update balances
        # Return: original size + P&L - exit fee
        returned_value = position.size_usd + pnl_usd - exit_fee
        self._quote_balance += returned_value
        self._total_fees_paid += exit_fee
        
        # Remove asset balance
        base_asset = position.symbol.replace(self.quote_asset, '')
        if base_asset in self._asset_balances:
            self._asset_balances[base_asset] -= position.quantity
            if self._asset_balances[base_asset] <= 0:
                del self._asset_balances[base_asset]
        
        # Move to closed
        self._closed_positions.append(position)
        del self._positions[position_id]
        
        # Create trade result
        result = TradeResult(
            position_id=position_id,
            symbol=position.symbol,
            direction=position.direction,
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            exit_time=position.exit_time,
            exit_price=exit_price,
            quantity=position.quantity,
            size_usd=position.size_usd,
            pnl_pct=pnl_pct,
            pnl_usd=pnl_usd,
            net_pnl_usd=net_pnl,
            fees_usd=position.total_fees_usd,
            close_reason=reason,
            hold_time_minutes=position.hold_time_minutes,
            highest_pnl_pct=position.highest_pnl_pct,
            signal_score=position.signal_score,
            signal_regime=position.signal_regime
        )
        
        self._trade_results.append(result)
        
        # Update peak/lowest
        current_total = self.total_balance
        if current_total > self._peak_balance:
            self._peak_balance = current_total
        if current_total < self._lowest_balance:
            self._lowest_balance = current_total
        
        # Log
        emoji = "🟢" if result.is_win else "🔴"
        log.trade(f"{emoji} CLOSE {position.direction} {position.symbol}: "
                 f"{position.quantity:.8f} @ {exit_price:.8f} | "
                 f"P&L: {net_pnl:+.2f} USD ({pnl_pct:+.2f}%) | "
                 f"Reason: {reason.value}")
        
        return result
    
    # =========================================================================
    # PRICE UPDATES
    # =========================================================================
    
    def update_prices(self, prices: Dict[str, float]):
        """
        Update prices for all positions.
        
        Args:
            prices: Dict of symbol → price
        """
        for position in self.open_positions:
            if position.symbol in prices:
                position.update_price(prices[position.symbol])
        
        self._last_update = datetime.now()
        
        # Update peak/lowest
        current_total = self.total_balance
        if current_total > self._peak_balance:
            self._peak_balance = current_total
        if current_total < self._lowest_balance:
            self._lowest_balance = current_total
    
    def update_position_price(self, position_id: str, price: float):
        """Update single position price."""
        position = self._positions.get(position_id)
        if position and position.is_open:
            position.update_price(price)
    
    # =========================================================================
    # TRAILING STOP MANAGEMENT
    # =========================================================================
    
    def update_trailing_stop(self, position_id: str) -> Optional[float]:
        """
        Update trailing stop for position.
        
        Returns:
            New stop price if updated, None otherwise
        """
        position = self._positions.get(position_id)
        
        if not position or not position.is_open:
            return None
        
        current_pnl = position.unrealized_pnl_pct
        
        # Check breakeven activation
        if not position.trailing_active:
            if current_pnl >= position.trailing_activation_pct:
                position.trailing_active = True
                position.breakeven_activated_at = datetime.now()
                
                # Set initial trail to breakeven + small buffer
                if position.direction == 'LONG':
                    position.current_trail_price = position.entry_price * 1.001
                    position.stop_loss_price = position.current_trail_price
                else:
                    position.current_trail_price = position.entry_price * 0.999
                    position.stop_loss_price = position.current_trail_price
                
                log.debug(f"Breakeven activated for {position.symbol} @ {position.stop_loss_price:.8f}")
                return position.stop_loss_price
        
        # Update trailing stop
        if position.trailing_active:
            trail_distance = position.trailing_distance_pct / 100
            
            if position.direction == 'LONG':
                new_trail = position.highest_price * (1 - trail_distance)
                
                if new_trail > position.stop_loss_price:
                    position.stop_loss_price = new_trail
                    position.current_trail_price = new_trail
                    log.debug(f"Trail updated for {position.symbol}: SL → {new_trail:.8f}")
                    return new_trail
            
            else:  # SHORT
                new_trail = position.lowest_price * (1 + trail_distance)
                
                if new_trail < position.stop_loss_price:
                    position.stop_loss_price = new_trail
                    position.current_trail_price = new_trail
                    return new_trail
        
        return None
    
    def check_stop_loss(self, position_id: str, current_price: float) -> bool:
        """
        Check if stop loss should be triggered.
        
        Returns:
            True if SL triggered
        """
        position = self._positions.get(position_id)
        
        if not position or not position.is_open:
            return False
        
        if position.direction == 'LONG':
            return current_price <= position.stop_loss_price
        else:
            return current_price >= position.stop_loss_price
    
    def check_take_profit(self, position_id: str, current_price: float) -> bool:
        """
        Check if take profit should be triggered.
        
        Returns:
            True if TP triggered
        """
        position = self._positions.get(position_id)
        
        if not position or not position.is_open:
            return False
        
        if position.take_profit_price is None:
            return False
        
        if position.direction == 'LONG':
            return current_price >= position.take_profit_price
        else:
            return current_price <= position.take_profit_price
    
    def check_timeout(self, position_id: str, timeout_minutes: int) -> bool:
        """Check if position timed out."""
        position = self._positions.get(position_id)
        
        if not position or not position.is_open:
            return False
        
        return position.hold_time_minutes >= timeout_minutes
    
    # =========================================================================
    # STATISTICS
    # =========================================================================
    
    def get_stats(self) -> dict:
        """Get portfolio statistics."""
        total_trades = len(self._trade_results)
        wins = sum(1 for r in self._trade_results if r.is_win)
        losses = sum(1 for r in self._trade_results if r.is_loss)
        
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        # Average P&L
        avg_pnl = sum(r.net_pnl_usd for r in self._trade_results) / total_trades if total_trades > 0 else 0
        avg_win = sum(r.net_pnl_usd for r in self._trade_results if r.is_win) / wins if wins > 0 else 0
        avg_loss = sum(r.net_pnl_usd for r in self._trade_results if r.is_loss) / losses if losses > 0 else 0
        
        # Best/worst
        if self._trade_results:
            best = max(r.net_pnl_usd for r in self._trade_results)
            worst = min(r.net_pnl_usd for r in self._trade_results)
            best_pct = max(r.pnl_pct for r in self._trade_results)
            worst_pct = min(r.pnl_pct for r in self._trade_results)
        else:
            best = worst = best_pct = worst_pct = 0
        
        # Profit factor
        gross_profit = sum(r.net_pnl_usd for r in self._trade_results if r.is_win)
        gross_loss = abs(sum(r.net_pnl_usd for r in self._trade_results if r.is_loss))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf') if gross_profit > 0 else 0
        
        return {
            'initial_balance': self._initial_balance,
            'current_balance': round(self.total_balance, 2),
            'quote_balance': round(self._quote_balance, 2),
            'unrealized_pnl': round(self.unrealized_pnl, 2),
            'realized_pnl': round(self.realized_pnl, 2),
            'total_pnl': round(self.total_pnl, 2),
            'total_pnl_pct': round(self.total_pnl_pct, 2),
            'peak_balance': round(self._peak_balance, 2),
            'drawdown_pct': round(self.drawdown_pct, 2),
            'max_drawdown_pct': round(self.max_drawdown_pct, 2),
            'total_trades': total_trades,
            'wins': wins,
            'losses': losses,
            'win_rate': round(win_rate, 1),
            'profit_factor': round(profit_factor, 2),
            'avg_pnl': round(avg_pnl, 2),
            'avg_win': round(avg_win, 2),
            'avg_loss': round(avg_loss, 2),
            'best_trade': round(best, 2),
            'worst_trade': round(worst, 2),
            'best_trade_pct': round(best_pct, 2),
            'worst_trade_pct': round(worst_pct, 2),
            'total_fees_paid': round(self._total_fees_paid, 2),
            'open_positions': self.open_position_count,
            'created_at': self._created_at.isoformat(),
        }
    
    # =========================================================================
    # PERSISTENCE
    # =========================================================================
    
    def to_dict(self) -> dict:
        """Serialize portfolio state."""
        return {
            'quote_asset': self.quote_asset,
            'taker_fee_pct': self.taker_fee_pct,
            'maker_fee_pct': self.maker_fee_pct,
            'max_positions': self.max_positions,
            'initial_balance': self._initial_balance,
            'quote_balance': self._quote_balance,
            'asset_balances': self._asset_balances,
            'peak_balance': self._peak_balance,
            'lowest_balance': self._lowest_balance,
            'total_fees_paid': self._total_fees_paid,
            'created_at': self._created_at.isoformat(),
            'positions': {pid: p.to_dict() for pid, p in self._positions.items()},
            'closed_positions': [p.to_dict() for p in self._closed_positions],
            'trade_results': [r.to_dict() for r in self._trade_results],
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'PaperPortfolio':
        """Deserialize portfolio state."""
        portfolio = cls(
            initial_balance=data['initial_balance'],
            quote_asset=data['quote_asset'],
            taker_fee_pct=data['taker_fee_pct'],
            maker_fee_pct=data['maker_fee_pct'],
            max_positions=data['max_positions']
        )
        
        portfolio._quote_balance = data['quote_balance']
        portfolio._asset_balances = data['asset_balances']
        portfolio._peak_balance = data['peak_balance']
        portfolio._lowest_balance = data['lowest_balance']
        portfolio._total_fees_paid = data['total_fees_paid']
        portfolio._created_at = datetime.fromisoformat(data['created_at'])
        
        # Load positions
        for pid, pdata in data.get('positions', {}).items():
            portfolio._positions[pid] = Position.from_dict(pdata)
        
        # Load closed positions
        for pdata in data.get('closed_positions', []):
            portfolio._closed_positions.append(Position.from_dict(pdata))
        
        # Trade results would need separate loading if needed
        
        return portfolio
    
    def save(self, path: str):
        """Save portfolio to file."""
        with open(path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
        log.info(f"Portfolio saved to {path}")
    
    @classmethod
    def load(cls, path: str) -> 'PaperPortfolio':
        """Load portfolio from file."""
        with open(path, 'r') as f:
            data = json.load(f)
        log.info(f"Portfolio loaded from {path}")
        return cls.from_dict(data)


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def create_portfolio(
    initial_balance: float = 1000.0,
    fee_pct: float = 0.04,
    max_positions: int = 1
) -> PaperPortfolio:
    """Create paper portfolio."""
    return PaperPortfolio(
        initial_balance=initial_balance,
        taker_fee_pct=fee_pct,
        max_positions=max_positions
    )