"""
Abstract base class for exchange implementations.

Defines standard interface for all exchange connectors:
- Market data (ticker, orderbook, OHLCV)
- Account data (balance, orders)
- Trading (place, cancel orders)
- Symbol info (filters, limits)

All implementations must handle:
- Rate limiting
- Error handling with retries
- Logging
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum
import time


# =============================================================================
# ENUMS
# =============================================================================

class OrderSide(Enum):
    """Order side."""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    """Order type."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"
    STOP_LOSS_LIMIT = "STOP_LOSS_LIMIT"
    TAKE_PROFIT = "TAKE_PROFIT"
    TAKE_PROFIT_LIMIT = "TAKE_PROFIT_LIMIT"


class OrderStatus(Enum):
    """Order status."""
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class Ticker:
    """Current ticker data."""
    symbol: str
    bid: float                      # Best bid price
    ask: float                      # Best ask price
    last: float                     # Last trade price
    volume_24h: float               # 24h volume in quote currency
    change_24h_pct: float           # 24h price change %
    high_24h: float                 # 24h high
    low_24h: float                  # 24h low
    timestamp: datetime
    
    @property
    def spread(self) -> float:
        """Bid-ask spread."""
        return self.ask - self.bid
    
    @property
    def spread_pct(self) -> float:
        """Spread as percentage."""
        if self.bid == 0:
            return 0.0
        return (self.spread / self.bid) * 100
    
    @property
    def mid_price(self) -> float:
        """Mid price between bid and ask."""
        return (self.bid + self.ask) / 2
    
    def to_dict(self) -> dict:
        return {
            'symbol': self.symbol,
            'bid': self.bid,
            'ask': self.ask,
            'last': self.last,
            'spread_pct': round(self.spread_pct, 4),
            'volume_24h': round(self.volume_24h, 2),
            'change_24h_pct': round(self.change_24h_pct, 2),
        }


@dataclass
class OHLCV:
    """Single OHLCV candle."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    
    def to_dict(self) -> dict:
        return {
            'timestamp': self.timestamp.isoformat(),
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'close': self.close,
            'volume': self.volume,
        }


@dataclass
class Balance:
    """Account balance for single asset."""
    asset: str
    free: float                     # Available balance
    locked: float                   # In open orders
    
    @property
    def total(self) -> float:
        """Total balance."""
        return self.free + self.locked
    
    def to_dict(self) -> dict:
        return {
            'asset': self.asset,
            'free': self.free,
            'locked': self.locked,
            'total': self.total,
        }


@dataclass
class Order:
    """Order information."""
    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    status: OrderStatus
    price: float                    # Limit price (0 for market)
    quantity: float                 # Order quantity
    filled_quantity: float          # Filled quantity
    average_price: float            # Average fill price
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    @property
    def is_filled(self) -> bool:
        return self.status == OrderStatus.FILLED
    
    @property
    def is_open(self) -> bool:
        return self.status in [OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED]
    
    @property
    def fill_pct(self) -> float:
        """Percentage filled."""
        if self.quantity == 0:
            return 0.0
        return (self.filled_quantity / self.quantity) * 100
    
    def to_dict(self) -> dict:
        return {
            'order_id': self.order_id,
            'symbol': self.symbol,
            'side': self.side.value,
            'type': self.order_type.value,
            'status': self.status.value,
            'price': self.price,
            'quantity': self.quantity,
            'filled': self.filled_quantity,
            'avg_price': self.average_price,
            'fill_pct': round(self.fill_pct, 2),
        }


@dataclass
class SymbolInfo:
    """Trading symbol information."""
    symbol: str
    base_asset: str                 # e.g., BTC
    quote_asset: str                # e.g., USDT
    status: str                     # TRADING, HALT, etc.
    
    # Precision
    price_precision: int            # Decimal places for price
    quantity_precision: int         # Decimal places for quantity
    
    # Filters
    min_quantity: float             # Minimum order quantity
    max_quantity: float             # Maximum order quantity
    step_size: float                # Quantity step size
    min_notional: float             # Minimum order value (USD)
    tick_size: float                # Price tick size
    
    def to_dict(self) -> dict:
        return {
            'symbol': self.symbol,
            'base': self.base_asset,
            'quote': self.quote_asset,
            'status': self.status,
            'price_precision': self.price_precision,
            'quantity_precision': self.quantity_precision,
            'min_quantity': self.min_quantity,
            'min_notional': self.min_notional,
        }


# =============================================================================
# EXCEPTIONS
# =============================================================================

class ExchangeError(Exception):
    """Base exchange error."""
    pass


class RateLimitError(ExchangeError):
    """Rate limit exceeded."""
    pass


class InsufficientBalanceError(ExchangeError):
    """Insufficient balance for order."""
    pass


class InvalidOrderError(ExchangeError):
    """Invalid order parameters."""
    pass


class OrderNotFoundError(ExchangeError):
    """Order not found."""
    pass


class ConnectionError(ExchangeError):
    """Connection to exchange failed."""
    pass


class AuthenticationError(ExchangeError):
    """API key authentication failed."""
    pass


# =============================================================================
# ABSTRACT BASE CLASS
# =============================================================================

class ExchangeBase(ABC):
    """
    Abstract base class for exchange implementations.
    
    Usage:
        class BinanceExchange(ExchangeBase):
            def get_ticker(self, symbol: str) -> Ticker:
                # Implementation
                ...
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        testnet: bool = False,
        timeout_ms: int = 15000,
        retry_count: int = 3,
        retry_delay_ms: int = 1000
    ):
        """
        Initialize exchange.
        
        Args:
            api_key: API key (None for public endpoints only)
            api_secret: API secret
            testnet: Use testnet
            timeout_ms: Request timeout in milliseconds
            retry_count: Number of retries on failure
            retry_delay_ms: Delay between retries
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.timeout_ms = timeout_ms
        self.retry_count = retry_count
        self.retry_delay_ms = retry_delay_ms
        
        # Rate limiting
        self._last_request_time: float = 0
        self._min_request_interval: float = 0.1  # 100ms default
        
        # Cache
        self._symbol_cache: Dict[str, SymbolInfo] = {}
        self._symbol_cache_time: float = 0
        self._symbol_cache_ttl: float = 3600  # 1 hour
    
    # =========================================================================
    # ABSTRACT METHODS - Market Data
    # =========================================================================
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Exchange name."""
        pass
    
    @abstractmethod
    def get_ticker(self, symbol: str) -> Ticker:
        """
        Get current ticker data.
        
        Args:
            symbol: Trading symbol (e.g., 'BTCUSDT')
        
        Returns:
            Ticker instance
        
        Raises:
            ExchangeError: On API error
        """
        pass
    
    @abstractmethod
    def get_tickers(self, symbols: Optional[List[str]] = None) -> List[Ticker]:
        """
        Get multiple tickers.
        
        Args:
            symbols: List of symbols (None = all)
        
        Returns:
            List of Ticker instances
        """
        pass
    
    @abstractmethod
    def get_ohlcv(
        self,
        symbol: str,
        interval: str = '5m',
        limit: int = 100,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> List[OHLCV]:
        """
        Get OHLCV candlestick data.
        
        Args:
            symbol: Trading symbol
            interval: Candle interval ('1m', '5m', '15m', '1h', '4h', '1d')
            limit: Number of candles
            start_time: Start time
            end_time: End time
        
        Returns:
            List of OHLCV instances (oldest first)
        """
        pass
    
    @abstractmethod
    def get_orderbook(
        self,
        symbol: str,
        limit: int = 20
    ) -> Dict[str, List[List[float]]]:
        """
        Get order book.
        
        Args:
            symbol: Trading symbol
            limit: Number of levels
        
        Returns:
            {'bids': [[price, qty], ...], 'asks': [[price, qty], ...]}
        """
        pass
    
    # =========================================================================
    # ABSTRACT METHODS - Account Data
    # =========================================================================
    
    @abstractmethod
    def get_balance(self, asset: Optional[str] = None) -> List[Balance]:
        """
        Get account balance.
        
        Args:
            asset: Specific asset (None = all non-zero)
        
        Returns:
            List of Balance instances
        """
        pass
    
    @abstractmethod
    def get_open_orders(
        self,
        symbol: Optional[str] = None
    ) -> List[Order]:
        """
        Get open orders.
        
        Args:
            symbol: Filter by symbol (None = all)
        
        Returns:
            List of Order instances
        """
        pass
    
    @abstractmethod
    def get_order(self, symbol: str, order_id: str) -> Order:
        """
        Get specific order.
        
        Args:
            symbol: Trading symbol
            order_id: Order ID
        
        Returns:
            Order instance
        
        Raises:
            OrderNotFoundError: If order not found
        """
        pass
    
    # =========================================================================
    # ABSTRACT METHODS - Trading
    # =========================================================================
    
    @abstractmethod
    def place_market_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: Optional[float] = None,
        quote_quantity: Optional[float] = None
    ) -> Order:
        """
        Place market order.
        
        Args:
            symbol: Trading symbol
            side: BUY or SELL
            quantity: Base asset quantity (e.g., 0.001 BTC)
            quote_quantity: Quote asset quantity (e.g., 100 USDT)
        
        Returns:
            Order instance
        
        Note: Either quantity OR quote_quantity must be provided
        """
        pass
    
    @abstractmethod
    def place_limit_order(
        self,
        symbol: str,
        side: OrderSide,
        price: float,
        quantity: float
    ) -> Order:
        """
        Place limit order.
        
        Args:
            symbol: Trading symbol
            side: BUY or SELL
            price: Limit price
            quantity: Order quantity
        
        Returns:
            Order instance
        """
        pass
    
    @abstractmethod
    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """
        Cancel order.
        
        Args:
            symbol: Trading symbol
            order_id: Order ID
        
        Returns:
            True if cancelled successfully
        
        Raises:
            OrderNotFoundError: If order not found
        """
        pass
    
    @abstractmethod
    def cancel_all_orders(self, symbol: Optional[str] = None) -> int:
        """
        Cancel all open orders.
        
        Args:
            symbol: Filter by symbol (None = all symbols)
        
        Returns:
            Number of orders cancelled
        """
        pass
    
    # =========================================================================
    # ABSTRACT METHODS - Symbol Info
    # =========================================================================
    
    @abstractmethod
    def get_symbols(self, quote_asset: str = 'USDT') -> List[str]:
        """
        Get all trading symbols.
        
        Args:
            quote_asset: Filter by quote asset
        
        Returns:
            List of symbol names
        """
        pass
    
    @abstractmethod
    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        """
        Get symbol trading info.
        
        Args:
            symbol: Trading symbol
        
        Returns:
            SymbolInfo instance
        """
        pass
    
    # =========================================================================
    # COMMON METHODS (implemented in base)
    # =========================================================================
    
    def get_spread(self, symbol: str) -> float:
        """Get bid-ask spread percentage."""
        ticker = self.get_ticker(symbol)
        return ticker.spread_pct
    
    def get_price(self, symbol: str) -> float:
        """Get current price (last trade)."""
        ticker = self.get_ticker(symbol)
        return ticker.last
    
    def get_usdt_balance(self) -> float:
        """Get free USDT balance."""
        balances = self.get_balance('USDT')
        for b in balances:
            if b.asset == 'USDT':
                return b.free
        return 0.0
    
    def has_open_position(self, symbol: str) -> bool:
        """Check if there's an open position (non-zero balance)."""
        base_asset = symbol.replace('USDT', '')
        balances = self.get_balance(base_asset)
        
        for b in balances:
            if b.asset == base_asset and b.total > 0:
                return True
        return False
    
    # =========================================================================
    # RATE LIMITING
    # =========================================================================
    
    def _rate_limit(self):
        """Apply rate limiting."""
        elapsed = time.time() - self._last_request_time
        
        if elapsed < self._min_request_interval:
            sleep_time = self._min_request_interval - elapsed
            time.sleep(sleep_time)
        
        self._last_request_time = time.time()
    
    def set_rate_limit(self, requests_per_second: float):
        """Set rate limit."""
        self._min_request_interval = 1.0 / requests_per_second
    
    # =========================================================================
    # RETRY LOGIC
    # =========================================================================
    
    def _retry(self, func, *args, **kwargs):
        """
        Execute function with retry logic.
        
        Args:
            func: Function to execute
            *args, **kwargs: Function arguments
        
        Returns:
            Function result
        
        Raises:
            Last exception if all retries fail
        """
        last_error = None
        
        for attempt in range(self.retry_count):
            try:
                self._rate_limit()
                return func(*args, **kwargs)
            
            except RateLimitError as e:
                # Wait longer for rate limit
                wait_time = (self.retry_delay_ms / 1000) * (attempt + 2)
                time.sleep(wait_time)
                last_error = e
            
            except (ConnectionError, TimeoutError) as e:
                # Standard retry
                wait_time = (self.retry_delay_ms / 1000) * (attempt + 1)
                time.sleep(wait_time)
                last_error = e
            
            except ExchangeError as e:
                # Don't retry for invalid orders, auth errors, etc.
                raise
        
        # All retries failed
        raise last_error
    
    # =========================================================================
    # QUANTITY FORMATTING
    # =========================================================================
    
    def format_quantity(self, symbol: str, quantity: float) -> float:
        """
        Format quantity according to symbol rules.
        
        Args:
            symbol: Trading symbol
            quantity: Raw quantity
        
        Returns:
            Formatted quantity
        """
        info = self.get_symbol_info(symbol)
        
        # Apply step size
        step = info.step_size
        quantity = (quantity // step) * step
        
        # Apply precision
        quantity = round(quantity, info.quantity_precision)
        
        return quantity
    
    def format_price(self, symbol: str, price: float) -> float:
        """
        Format price according to symbol rules.
        
        Args:
            symbol: Trading symbol
            price: Raw price
        
        Returns:
            Formatted price
        """
        info = self.get_symbol_info(symbol)
        
        # Apply tick size
        tick = info.tick_size
        price = round(price / tick) * tick
        
        # Apply precision
        price = round(price, info.price_precision)
        
        return price
    
    def validate_order(
        self,
        symbol: str,
        quantity: float,
        price: Optional[float] = None
    ) -> tuple[bool, Optional[str]]:
        """
        Validate order parameters.
        
        Args:
            symbol: Trading symbol
            quantity: Order quantity
            price: Order price (for limit orders)
        
        Returns:
            (is_valid, error_message)
        """
        info = self.get_symbol_info(symbol)
        
        # Check minimum quantity
        if quantity < info.min_quantity:
            return False, f"Quantity {quantity} below minimum {info.min_quantity}"
        
        # Check maximum quantity
        if quantity > info.max_quantity:
            return False, f"Quantity {quantity} above maximum {info.max_quantity}"
        
        # Check minimum notional
        if price:
            notional = quantity * price
            if notional < info.min_notional:
                return False, f"Notional {notional} below minimum {info.min_notional}"
        
        return True, None
    
    # =========================================================================
    # CONNECTION TEST
    # =========================================================================
    
    @abstractmethod
    def test_connection(self) -> bool:
        """
        Test exchange connection.
        
        Returns:
            True if connection successful
        """
        pass
    
    @abstractmethod
    def test_authentication(self) -> bool:
        """
        Test API key authentication.
        
        Returns:
            True if authentication successful
        """
        pass