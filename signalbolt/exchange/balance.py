"""
Balance tracker with history and P&L calculation.

Features:
- Real-time balance tracking
- Historical snapshots (configurable interval)
- Profit/loss calculation (USD and %)
- Transaction history
- Support for paper trading (virtual balance)
- CSV export for analysis
"""

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from pathlib import Path
import json
import csv
from enum import Enum

from signalbolt.exchange.base import Balance, ExchangeBase
from signalbolt.utils.logger import get_logger

log = get_logger('signalbolt.exchange.balance')


# =============================================================================
# ENUMS
# =============================================================================

class TransactionType(Enum):
    """Transaction type."""
    BUY = "BUY"
    SELL = "SELL"
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    FEE = "FEE"
    TRANSFER = "TRANSFER"


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class BalanceSnapshot:
    """Balance snapshot at a point in time."""
    
    timestamp: datetime
    asset: str
    free: float
    locked: float
    total: float
    usd_value: Optional[float] = None  # Value in USD (if price available)
    
    def to_dict(self) -> dict:
        return {
            'timestamp': self.timestamp.isoformat(),
            'asset': self.asset,
            'free': self.free,
            'locked': self.locked,
            'total': self.total,
            'usd_value': self.usd_value,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'BalanceSnapshot':
        data = dict(data)  # Copy to avoid mutation
        data['timestamp'] = datetime.fromisoformat(data['timestamp'])
        return cls(**data)


@dataclass
class Transaction:
    """Single transaction record."""
    
    timestamp: datetime
    tx_type: TransactionType
    asset: str
    amount: float
    price: Optional[float] = None  # Price per unit (for BUY/SELL)
    fee: float = 0.0
    fee_asset: str = ''
    symbol: Optional[str] = None  # Trading symbol (for BUY/SELL)
    order_id: Optional[str] = None
    notes: str = ''
    
    @property
    def usd_value(self) -> Optional[float]:
        """Calculate USD value."""
        if self.price is not None:
            return abs(self.amount) * self.price
        return None
    
    def to_dict(self) -> dict:
        return {
            'timestamp': self.timestamp.isoformat(),
            'type': self.tx_type.value,
            'asset': self.asset,
            'amount': self.amount,
            'price': self.price,
            'fee': self.fee,
            'fee_asset': self.fee_asset,
            'symbol': self.symbol,
            'order_id': self.order_id,
            'notes': self.notes,
            'usd_value': self.usd_value,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Transaction':
        data = dict(data)
        data['timestamp'] = datetime.fromisoformat(data['timestamp'])
        data['tx_type'] = TransactionType(data.pop('type'))
        data.pop('usd_value', None)  # Remove computed field
        return cls(**data)


@dataclass
class ProfitLoss:
    """Profit/loss calculation result."""
    
    starting_balance_usd: float
    current_balance_usd: float
    realized_pnl_usd: float      # From closed trades
    unrealized_pnl_usd: float    # From open positions
    total_pnl_usd: float
    total_pnl_pct: float
    fees_paid_usd: float
    
    def to_dict(self) -> dict:
        return asdict(self)


# =============================================================================
# BALANCE TRACKER
# =============================================================================

class BalanceTracker:
    """
    Track balances with history and P&L calculation.
    
    Usage:
        # Real-time tracking
        tracker = BalanceTracker(exchange, mode='live')
        tracker.update()  # Fetch from exchange
        
        # Paper trading
        tracker = BalanceTracker(None, mode='paper', initial_balance=1000.0)
        tracker.record_buy('BTC', 0.01, 67234.50, fee=0.67)
        
        # Get current balance
        usdt = tracker.get_balance('USDT')
        
        # Get P&L
        pnl = tracker.get_profit_loss()
        
        # Save session
        tracker.save('data/paper_sessions/session_001')
    """
    
    def __init__(
        self,
        exchange: Optional[ExchangeBase] = None,
        mode: str = 'live',
        initial_balance: float = 0.0,
        quote_asset: str = 'USDT',
        snapshot_interval_min: int = 30
    ):
        """
        Initialize tracker.
        
        Args:
            exchange: Exchange instance (None for paper trading)
            mode: 'live' or 'paper'
            initial_balance: Initial balance for paper trading
            quote_asset: Quote asset for P&L calculation
            snapshot_interval_min: Interval for automatic snapshots
        """
        self.exchange = exchange
        self.mode = mode
        self.quote_asset = quote_asset
        self.snapshot_interval = timedelta(minutes=snapshot_interval_min)
        
        # State
        self._balances: Dict[str, Balance] = {}
        self._snapshots: List[BalanceSnapshot] = []
        self._transactions: List[Transaction] = []
        self._last_snapshot_time: Optional[datetime] = None
        
        # P&L tracking
        self._starting_balance_usd: float = initial_balance
        self._realized_pnl_usd: float = 0.0
        
        # Paper trading: initialize virtual balance
        if mode == 'paper':
            self._balances[quote_asset] = Balance(
                asset=quote_asset,
                free=initial_balance,
                locked=0.0
            )
            self._take_snapshot()
        
        log.info(f"Balance tracker initialized (mode={mode}, initial={initial_balance})")
    
    # =========================================================================
    # BALANCE UPDATES
    # =========================================================================
    
    def update(self, force: bool = False):
        """
        Update balances from exchange.
        
        Args:
            force: Force update even if mode is paper
        """
        if self.mode == 'paper' and not force:
            # Paper mode - balances are tracked internally
            pass
        elif self.exchange:
            # Fetch from exchange
            try:
                balances = self.exchange.get_balance()
                
                for balance in balances:
                    self._balances[balance.asset] = balance
                
                log.debug(f"Updated balances: {len(balances)} assets")
            
            except Exception as e:
                log.error(f"Failed to update balances: {e}")
        
        # Auto-snapshot
        self._auto_snapshot()
    
    def get_balance(self, asset: str) -> Optional[Balance]:
        """Get balance for specific asset."""
        return self._balances.get(asset)
    
    def get_all_balances(self) -> List[Balance]:
        """Get all non-zero balances."""
        return [b for b in self._balances.values() if b.total > 0]
    
    def get_free_balance(self, asset: str) -> float:
        """Get free (available) balance."""
        balance = self._balances.get(asset)
        return balance.free if balance else 0.0
    
    def get_total_balance(self, asset: str) -> float:
        """Get total balance (free + locked)."""
        balance = self._balances.get(asset)
        return balance.total if balance else 0.0
    
    # =========================================================================
    # TRANSACTIONS
    # =========================================================================
    
    def record_buy(
        self,
        asset: str,
        quantity: float,
        price: float,
        fee: float = 0.0,
        fee_asset: Optional[str] = None,
        symbol: Optional[str] = None,
        order_id: Optional[str] = None,
        notes: str = ''
    ):
        """
        Record buy transaction (paper mode).
        
        Args:
            asset: Asset bought (e.g., 'BTC')
            quantity: Quantity bought
            price: Price per unit
            fee: Fee paid
            fee_asset: Asset used for fee (default: quote_asset)
            symbol: Trading symbol
            order_id: Order ID
            notes: Additional notes
        """
        if fee_asset is None:
            fee_asset = self.quote_asset
        
        # Calculate cost
        cost = quantity * price + fee
        
        # Update balances
        if self.mode == 'paper':
            # Deduct quote asset
            quote_balance = self._balances.get(self.quote_asset)
            if not quote_balance or quote_balance.free < cost:
                raise ValueError(f"Insufficient {self.quote_asset} balance")
            
            quote_balance.free -= cost
            
            # Add base asset
            if asset not in self._balances:
                self._balances[asset] = Balance(asset=asset, free=0.0, locked=0.0)
            
            self._balances[asset].free += quantity
        
        # Record transaction
        tx = Transaction(
            timestamp=datetime.now(),
            tx_type=TransactionType.BUY,
            asset=asset,
            amount=quantity,
            price=price,
            fee=fee,
            fee_asset=fee_asset,
            symbol=symbol,
            order_id=order_id,
            notes=notes
        )
        self._transactions.append(tx)
        
        log.info(f"Recorded BUY: {quantity} {asset} @ {price} (fee: {fee})")
    
    def record_sell(
        self,
        asset: str,
        quantity: float,
        price: float,
        fee: float = 0.0,
        fee_asset: Optional[str] = None,
        symbol: Optional[str] = None,
        order_id: Optional[str] = None,
        notes: str = ''
    ):
        """
        Record sell transaction (paper mode).
        
        Args:
            asset: Asset sold
            quantity: Quantity sold
            price: Price per unit
            fee: Fee paid
            fee_asset: Asset used for fee
            symbol: Trading symbol
            order_id: Order ID
            notes: Additional notes
        """
        if fee_asset is None:
            fee_asset = self.quote_asset
        
        # Calculate proceeds
        proceeds = quantity * price - fee
        
        # Update balances
        if self.mode == 'paper':
            # Deduct base asset
            base_balance = self._balances.get(asset)
            if not base_balance or base_balance.free < quantity:
                raise ValueError(f"Insufficient {asset} balance")
            
            base_balance.free -= quantity
            
            # Add quote asset
            if self.quote_asset not in self._balances:
                self._balances[self.quote_asset] = Balance(
                    asset=self.quote_asset, free=0.0, locked=0.0
                )
            
            self._balances[self.quote_asset].free += proceeds
        
        # Calculate realized P&L
        # (Simplified - assumes FIFO, full implementation would track cost basis)
        realized_pnl = proceeds - (quantity * price)  # Will be enhanced with cost basis
        self._realized_pnl_usd += realized_pnl
        
        # Record transaction
        tx = Transaction(
            timestamp=datetime.now(),
            tx_type=TransactionType.SELL,
            asset=asset,
            amount=-quantity,  # Negative for sell
            price=price,
            fee=fee,
            fee_asset=fee_asset,
            symbol=symbol,
            order_id=order_id,
            notes=notes
        )
        self._transactions.append(tx)
        
        log.info(f"Recorded SELL: {quantity} {asset} @ {price} (fee: {fee}, PnL: {realized_pnl:.2f})")
    
    def get_transactions(
        self,
        asset: Optional[str] = None,
        tx_type: Optional[TransactionType] = None,
        since: Optional[datetime] = None,
        limit: Optional[int] = None
    ) -> List[Transaction]:
        """
        Get transaction history.
        
        Args:
            asset: Filter by asset
            tx_type: Filter by type
            since: Filter by date
            limit: Limit results
        
        Returns:
            List of transactions (newest first)
        """
        txs = self._transactions.copy()
        
        # Filter
        if asset:
            txs = [tx for tx in txs if tx.asset == asset]
        
        if tx_type:
            txs = [tx for tx in txs if tx.tx_type == tx_type]
        
        if since:
            txs = [tx for tx in txs if tx.timestamp >= since]
        
        # Sort (newest first)
        txs.sort(key=lambda x: x.timestamp, reverse=True)
        
        # Limit
        if limit:
            txs = txs[:limit]
        
        return txs
    
    # =========================================================================
    # SNAPSHOTS
    # =========================================================================
    
    def _auto_snapshot(self):
        """Take automatic snapshot if interval elapsed."""
        now = datetime.now()
        
        if self._last_snapshot_time is None:
            self._take_snapshot()
            return
        
        if now - self._last_snapshot_time >= self.snapshot_interval:
            self._take_snapshot()
    
    def _take_snapshot(self):
        """Take balance snapshot."""
        now = datetime.now()
        
        for asset, balance in self._balances.items():
            if balance.total > 0:
                # Calculate USD value (if possible)
                usd_value = None
                if self.exchange and asset != self.quote_asset:
                    try:
                        symbol = f"{asset}{self.quote_asset}"
                        ticker = self.exchange.get_ticker(symbol)
                        usd_value = balance.total * ticker.last
                    except:
                        pass
                elif asset == self.quote_asset:
                    usd_value = balance.total
                
                snapshot = BalanceSnapshot(
                    timestamp=now,
                    asset=asset,
                    free=balance.free,
                    locked=balance.locked,
                    total=balance.total,
                    usd_value=usd_value
                )
                self._snapshots.append(snapshot)
        
        self._last_snapshot_time = now
        log.debug(f"Snapshot taken: {len(self._balances)} assets")
    
    def get_snapshots(
        self,
        asset: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: Optional[int] = None
    ) -> List[BalanceSnapshot]:
        """
        Get balance snapshots.
        
        Args:
            asset: Filter by asset
            since: Filter by date
            limit: Limit results
        
        Returns:
            List of snapshots (newest first)
        """
        snapshots = self._snapshots.copy()
        
        if asset:
            snapshots = [s for s in snapshots if s.asset == asset]
        
        if since:
            snapshots = [s for s in snapshots if s.timestamp >= since]
        
        snapshots.sort(key=lambda x: x.timestamp, reverse=True)
        
        if limit:
            snapshots = snapshots[:limit]
        
        return snapshots
    
    # =========================================================================
    # PROFIT/LOSS
    # =========================================================================
    
    def get_profit_loss(self) -> ProfitLoss:
        """
        Calculate current profit/loss.
        
        Returns:
            ProfitLoss instance
        """
        # Current balance in USD
        current_usd = self._calculate_total_balance_usd()
        
        # Unrealized P&L (from open positions)
        unrealized_pnl = self._calculate_unrealized_pnl()
        
        # Total P&L
        total_pnl_usd = current_usd - self._starting_balance_usd
        total_pnl_pct = (total_pnl_usd / self._starting_balance_usd) * 100 if self._starting_balance_usd > 0 else 0
        
        # Fees
        fees_paid = sum(tx.fee for tx in self._transactions if tx.fee_asset == self.quote_asset)
        
        return ProfitLoss(
            starting_balance_usd=self._starting_balance_usd,
            current_balance_usd=current_usd,
            realized_pnl_usd=self._realized_pnl_usd,
            unrealized_pnl_usd=unrealized_pnl,
            total_pnl_usd=total_pnl_usd,
            total_pnl_pct=total_pnl_pct,
            fees_paid_usd=fees_paid
        )
    
    def _calculate_total_balance_usd(self) -> float:
        """Calculate total balance in USD."""
        total = 0.0
        
        for asset, balance in self._balances.items():
            if balance.total == 0:
                continue
            
            if asset == self.quote_asset:
                total += balance.total
            elif self.exchange:
                try:
                    symbol = f"{asset}{self.quote_asset}"
                    ticker = self.exchange.get_ticker(symbol)
                    total += balance.total * ticker.last
                except:
                    log.warning(f"Could not get price for {asset}")
        
        return total
    
    def _calculate_unrealized_pnl(self) -> float:
        """Calculate unrealized P&L from open positions."""
        # This is simplified - full implementation would track cost basis
        # For now, return 0 (will be enhanced with position tracking)
        return 0.0
    
    # =========================================================================
    # PERSISTENCE
    # =========================================================================
    
    def save(self, session_dir: Path):
        """
        Save tracker state to session directory.
        
        Args:
            session_dir: Session directory path
        """
        session_dir = Path(session_dir)
        session_dir.mkdir(parents=True, exist_ok=True)
        
        # Save balances
        balances_file = session_dir / 'balances.json'
        with open(balances_file, 'w') as f:
            json.dump(
                {asset: asdict(balance) for asset, balance in self._balances.items()},
                f,
                indent=2
            )
        
        # Save transactions
        transactions_file = session_dir / 'transactions.json'
        with open(transactions_file, 'w') as f:
            json.dump(
                [tx.to_dict() for tx in self._transactions],
                f,
                indent=2
            )
        
        # Save transactions as CSV
        transactions_csv = session_dir / 'transactions.csv'
        if self._transactions:
            with open(transactions_csv, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=self._transactions[0].to_dict().keys())
                writer.writeheader()
                writer.writerows([tx.to_dict() for tx in self._transactions])
        
        # Save snapshots
        snapshots_file = session_dir / 'snapshots.json'
        with open(snapshots_file, 'w') as f:
            json.dump(
                [s.to_dict() for s in self._snapshots],
                f,
                indent=2
            )
        
        # Save metadata
        metadata = {
            'mode': self.mode,
            'quote_asset': self.quote_asset,
            'starting_balance_usd': self._starting_balance_usd,
            'realized_pnl_usd': self._realized_pnl_usd,
            'last_update': datetime.now().isoformat(),
        }
        
        metadata_file = session_dir / 'balance_metadata.json'
        with open(metadata_file, 'w') as f:
            json.dump(metadata, f, indent=2)
        
        log.info(f"Tracker state saved to {session_dir}")
    
    @classmethod
    def load(
        cls,
        session_dir: Path,
        exchange: Optional[ExchangeBase] = None
    ) -> 'BalanceTracker':
        """
        Load tracker state from session directory.
        
        Args:
            session_dir: Session directory path
            exchange: Exchange instance (for live mode)
        
        Returns:
            BalanceTracker instance
        """
        session_dir = Path(session_dir)
        
        # Load metadata
        metadata_file = session_dir / 'balance_metadata.json'
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)
        
        # Create tracker
        tracker = cls(
            exchange=exchange,
            mode=metadata['mode'],
            initial_balance=metadata['starting_balance_usd'],
            quote_asset=metadata['quote_asset']
        )
        
        tracker._realized_pnl_usd = metadata['realized_pnl_usd']
        
        # Load balances
        balances_file = session_dir / 'balances.json'
        if balances_file.exists():
            with open(balances_file, 'r') as f:
                balances_data = json.load(f)
            
            tracker._balances = {
                asset: Balance(**data) for asset, data in balances_data.items()
            }
        
        # Load transactions
        transactions_file = session_dir / 'transactions.json'
        if transactions_file.exists():
            with open(transactions_file, 'r') as f:
                transactions_data = json.load(f)
            
            tracker._transactions = [
                Transaction.from_dict(tx) for tx in transactions_data
            ]
        
        # Load snapshots
        snapshots_file = session_dir / 'snapshots.json'
        if snapshots_file.exists():
            with open(snapshots_file, 'r') as f:
                snapshots_data = json.load(f)
            
            tracker._snapshots = [
                BalanceSnapshot.from_dict(s) for s in snapshots_data
            ]
        
        log.info(f"Tracker state loaded from {session_dir}")
        return tracker
    
    # =========================================================================
    # STATISTICS
    # =========================================================================
    
    def get_statistics(self) -> dict:
        """Get tracker statistics."""
        pnl = self.get_profit_loss()
        
        # Transaction counts
        buy_count = len([tx for tx in self._transactions if tx.tx_type == TransactionType.BUY])
        sell_count = len([tx for tx in self._transactions if tx.tx_type == TransactionType.SELL])
        
        # Total volume
        total_volume = sum(
            tx.usd_value for tx in self._transactions
            if tx.usd_value and tx.tx_type in [TransactionType.BUY, TransactionType.SELL]
        )
        
        return {
            'mode': self.mode,
            'starting_balance': pnl.starting_balance_usd,
            'current_balance': pnl.current_balance_usd,
            'total_pnl_usd': pnl.total_pnl_usd,
            'total_pnl_pct': pnl.total_pnl_pct,
            'realized_pnl_usd': pnl.realized_pnl_usd,
            'unrealized_pnl_usd': pnl.unrealized_pnl_usd,
            'fees_paid_usd': pnl.fees_paid_usd,
            'transaction_count': len(self._transactions),
            'buy_count': buy_count,
            'sell_count': sell_count,
            'total_volume_usd': total_volume,
            'snapshot_count': len(self._snapshots),
            'assets_held': len([b for b in self._balances.values() if b.total > 0]),
        }


# =============================================================================
# CONVENIENCE FUNCTION
# =============================================================================

def create_balance_tracker(
    exchange: Optional[ExchangeBase] = None,
    mode: str = 'live',
    initial_balance: float = 0.0
) -> BalanceTracker:
    """Create balance tracker instance."""
    return BalanceTracker(
        exchange=exchange,
        mode=mode,
        initial_balance=initial_balance
    )