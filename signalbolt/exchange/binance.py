"""
Binance exchange implementation.

Features:
- Spot trading
- Testnet support
- All base exchange methods
- Rate limiting (1200 requests/min)
- Error handling

Uses ccxt library for API communication.
"""

import ccxt
import pandas as pd
from datetime import datetime
from typing import Optional, List, Dict, Any

from signalbolt.exchange.base import (
    ExchangeBase,
    Ticker,
    OHLCV,
    Balance,
    Order,
    SymbolInfo,
    OrderSide,
    OrderType,
    OrderStatus,
    ExchangeError,
    RateLimitError,
    InsufficientBalanceError,
    InvalidOrderError,
    OrderNotFoundError,
    ConnectionError,
    AuthenticationError,
)
from signalbolt.utils.logger import get_logger

log = get_logger('signalbolt.exchange.binance')


# =============================================================================
# BINANCE EXCHANGE
# =============================================================================

class BinanceExchange(ExchangeBase):
    """
    Binance exchange implementation.
    
    Usage:
        # Public endpoints only
        exchange = BinanceExchange()
        ticker = exchange.get_ticker('BTCUSDT')
        
        # With API keys (for trading)
        exchange = BinanceExchange(
            api_key='xxx',
            api_secret='yyy',
            testnet=True  # Use testnet for testing
        )
        balance = exchange.get_balance('USDT')
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
        Initialize Binance exchange.
        
        Args:
            api_key: Binance API key
            api_secret: Binance API secret
            testnet: Use Binance testnet
            timeout_ms: Request timeout
            retry_count: Number of retries
            retry_delay_ms: Delay between retries
        """
        super().__init__(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
            timeout_ms=timeout_ms,
            retry_count=retry_count,
            retry_delay_ms=retry_delay_ms
        )
        
        # Initialize ccxt
        self._client = self._create_client()
        
        # Binance rate limit: 1200 requests/minute = 20/sec
        self.set_rate_limit(15)  # Conservative: 15 req/sec
        
        log.info(f"Binance exchange initialized (testnet={testnet})")
    
    def _create_client(self) -> ccxt.binance:
        """Create ccxt client."""
        config = {
            'timeout': self.timeout_ms,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',
                'adjustForTimeDifference': True,
            }
        }

        if self.api_key and self.api_secret:
            config['apiKey'] = self.api_key
            config['secret'] = self.api_secret

        # Testnet URLs
        if self.testnet:
            config['options']['testnet'] = True
            config['urls'] = {
                'api': {
                    'public': 'https://testnet.binance.vision/api/v3',
                    'private': 'https://testnet.binance.vision/api/v3',
                }
            }

        return ccxt.binance(config)
    
    # =========================================================================
    # PROPERTIES
    # =========================================================================
    
    @property
    def name(self) -> str:
        return "Binance" + (" Testnet" if self.testnet else "")
    
    # =========================================================================
    # MARKET DATA
    # =========================================================================
    
    def get_ticker(self, symbol: str) -> Ticker:
        """Get current ticker."""
        try:
            data = self._retry(self._client.fetch_ticker, symbol)
            
            return Ticker(
                symbol=symbol,
                bid=float(data.get('bid', 0) or 0),
                ask=float(data.get('ask', 0) or 0),
                last=float(data.get('last', 0) or 0),
                volume_24h=float(data.get('quoteVolume', 0) or 0),
                change_24h_pct=float(data.get('percentage', 0) or 0),
                high_24h=float(data.get('high', 0) or 0),
                low_24h=float(data.get('low', 0) or 0),
                timestamp=datetime.now()
            )
        
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(f"Rate limit exceeded: {e}")
        except ccxt.NetworkError as e:
            raise ConnectionError(f"Network error: {e}")
        except ccxt.ExchangeError as e:
            raise ExchangeError(f"Exchange error: {e}")
    
    def get_tickers(self, symbols: Optional[List[str]] = None) -> List[Ticker]:
        """Get multiple tickers."""
        try:
            data = self._retry(self._client.fetch_tickers, symbols)
            
            tickers = []
            for symbol, ticker_data in data.items():
                if symbols is None or symbol in symbols:
                    tickers.append(Ticker(
                        symbol=symbol,
                        bid=float(ticker_data.get('bid', 0) or 0),
                        ask=float(ticker_data.get('ask', 0) or 0),
                        last=float(ticker_data.get('last', 0) or 0),
                        volume_24h=float(ticker_data.get('quoteVolume', 0) or 0),
                        change_24h_pct=float(ticker_data.get('percentage', 0) or 0),
                        high_24h=float(ticker_data.get('high', 0) or 0),
                        low_24h=float(ticker_data.get('low', 0) or 0),
                        timestamp=datetime.now()
                    ))
            
            return tickers
        
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(f"Rate limit exceeded: {e}")
        except ccxt.NetworkError as e:
            raise ConnectionError(f"Network error: {e}")
        except ccxt.ExchangeError as e:
            raise ExchangeError(f"Exchange error: {e}")
    
    def get_ohlcv(
        self,
        symbol: str,
        interval: str = '5m',
        limit: int = 100,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> List[OHLCV]:
        """Get OHLCV candlestick data."""
        try:
            # Convert datetime to timestamp
            since = None
            if start_time:
                since = int(start_time.timestamp() * 1000)
            
            params = {}
            if end_time:
                params['endTime'] = int(end_time.timestamp() * 1000)
            
            data = self._retry(
                self._client.fetch_ohlcv,
                symbol,
                interval,
                since,
                limit,
                params
            )
            
            candles = []
            for candle in data:
                candles.append(OHLCV(
                    timestamp=datetime.fromtimestamp(candle[0] / 1000),
                    open=float(candle[1]),
                    high=float(candle[2]),
                    low=float(candle[3]),
                    close=float(candle[4]),
                    volume=float(candle[5])
                ))
            
            return candles
        
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(f"Rate limit exceeded: {e}")
        except ccxt.NetworkError as e:
            raise ConnectionError(f"Network error: {e}")
        except ccxt.ExchangeError as e:
            raise ExchangeError(f"Exchange error: {e}")
    
    def get_ohlcv_df(
        self,
        symbol: str,
        interval: str = '5m',
        limit: int = 100,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> pd.DataFrame:
        """
        Get OHLCV as pandas DataFrame.
        
        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        candles = self.get_ohlcv(symbol, interval, limit, start_time, end_time)
        
        df = pd.DataFrame([c.to_dict() for c in candles])
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        
        return df
    
    def get_orderbook(
        self,
        symbol: str,
        limit: int = 20
    ) -> Dict[str, List[List[float]]]:
        """Get order book."""
        try:
            data = self._retry(self._client.fetch_order_book, symbol, limit)
            
            return {
                'bids': [[float(b[0]), float(b[1])] for b in data.get('bids', [])],
                'asks': [[float(a[0]), float(a[1])] for a in data.get('asks', [])]
            }
        
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(f"Rate limit exceeded: {e}")
        except ccxt.NetworkError as e:
            raise ConnectionError(f"Network error: {e}")
        except ccxt.ExchangeError as e:
            raise ExchangeError(f"Exchange error: {e}")
    
    # =========================================================================
    # ACCOUNT DATA
    # =========================================================================
    
    def get_balance(self, asset: Optional[str] = None) -> List[Balance]:
        """Get account balance."""
        if not self.api_key:
            raise AuthenticationError("API key required for balance")
        
        try:
            data = self._retry(self._client.fetch_balance)
            
            balances = []
            for currency, balance_data in data.items():
                if currency in ['info', 'free', 'used', 'total', 'timestamp', 'datetime']:
                    continue
                
                free = float(balance_data.get('free', 0) or 0)
                locked = float(balance_data.get('used', 0) or 0)
                
                # Filter by asset if specified
                if asset and currency != asset:
                    continue
                
                # Skip zero balances unless specifically requested
                if not asset and (free + locked) == 0:
                    continue
                
                balances.append(Balance(
                    asset=currency,
                    free=free,
                    locked=locked
                ))
            
            return balances
        
        except ccxt.AuthenticationError as e:
            raise AuthenticationError(f"Authentication failed: {e}")
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(f"Rate limit exceeded: {e}")
        except ccxt.NetworkError as e:
            raise ConnectionError(f"Network error: {e}")
        except ccxt.ExchangeError as e:
            raise ExchangeError(f"Exchange error: {e}")
    
    def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Get open orders."""
        if not self.api_key:
            raise AuthenticationError("API key required")
        
        try:
            data = self._retry(self._client.fetch_open_orders, symbol)
            
            return [self._parse_order(o) for o in data]
        
        except ccxt.AuthenticationError as e:
            raise AuthenticationError(f"Authentication failed: {e}")
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(f"Rate limit exceeded: {e}")
        except ccxt.NetworkError as e:
            raise ConnectionError(f"Network error: {e}")
        except ccxt.ExchangeError as e:
            raise ExchangeError(f"Exchange error: {e}")
    
    def get_order(self, symbol: str, order_id: str) -> Order:
        """Get specific order."""
        if not self.api_key:
            raise AuthenticationError("API key required")
        
        try:
            data = self._retry(self._client.fetch_order, order_id, symbol)
            return self._parse_order(data)
        
        except ccxt.OrderNotFound as e:
            raise OrderNotFoundError(f"Order {order_id} not found")
        except ccxt.AuthenticationError as e:
            raise AuthenticationError(f"Authentication failed: {e}")
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(f"Rate limit exceeded: {e}")
        except ccxt.NetworkError as e:
            raise ConnectionError(f"Network error: {e}")
        except ccxt.ExchangeError as e:
            raise ExchangeError(f"Exchange error: {e}")
    
    # =========================================================================
    # TRADING
    # =========================================================================
    
    def place_market_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: Optional[float] = None,
        quote_quantity: Optional[float] = None
    ) -> Order:
        """Place market order."""
        if not self.api_key:
            raise AuthenticationError("API key required for trading")
        
        if quantity is None and quote_quantity is None:
            raise InvalidOrderError("Either quantity or quote_quantity required")
        
        try:
            params = {}
            
            if quote_quantity and side == OrderSide.BUY:
                # Buy with USDT amount
                params['quoteOrderQty'] = quote_quantity
                quantity = None
            
            order_side = 'buy' if side == OrderSide.BUY else 'sell'
            
            data = self._retry(
                self._client.create_order,
                symbol,
                'market',
                order_side,
                quantity,
                None,  # price (not used for market)
                params
            )
            
            log.info(f"Market {order_side} order placed: {symbol} qty={quantity or quote_quantity}")
            return self._parse_order(data)
        
        except ccxt.InsufficientFunds as e:
            raise InsufficientBalanceError(f"Insufficient balance: {e}")
        except ccxt.InvalidOrder as e:
            raise InvalidOrderError(f"Invalid order: {e}")
        except ccxt.AuthenticationError as e:
            raise AuthenticationError(f"Authentication failed: {e}")
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(f"Rate limit exceeded: {e}")
        except ccxt.NetworkError as e:
            raise ConnectionError(f"Network error: {e}")
        except ccxt.ExchangeError as e:
            raise ExchangeError(f"Exchange error: {e}")
    
    def place_limit_order(
        self,
        symbol: str,
        side: OrderSide,
        price: float,
        quantity: float
    ) -> Order:
        """Place limit order."""
        if not self.api_key:
            raise AuthenticationError("API key required for trading")
        
        try:
            order_side = 'buy' if side == OrderSide.BUY else 'sell'
            
            # Format price and quantity
            price = self.format_price(symbol, price)
            quantity = self.format_quantity(symbol, quantity)
            
            # Validate
            is_valid, error = self.validate_order(symbol, quantity, price)
            if not is_valid:
                raise InvalidOrderError(error)
            
            data = self._retry(
                self._client.create_order,
                symbol,
                'limit',
                order_side,
                quantity,
                price
            )
            
            log.info(f"Limit {order_side} order placed: {symbol} {quantity} @ {price}")
            return self._parse_order(data)
        
        except ccxt.InsufficientFunds as e:
            raise InsufficientBalanceError(f"Insufficient balance: {e}")
        except ccxt.InvalidOrder as e:
            raise InvalidOrderError(f"Invalid order: {e}")
        except ccxt.AuthenticationError as e:
            raise AuthenticationError(f"Authentication failed: {e}")
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(f"Rate limit exceeded: {e}")
        except ccxt.NetworkError as e:
            raise ConnectionError(f"Network error: {e}")
        except ccxt.ExchangeError as e:
            raise ExchangeError(f"Exchange error: {e}")
    
    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel order."""
        if not self.api_key:
            raise AuthenticationError("API key required")
        
        try:
            self._retry(self._client.cancel_order, order_id, symbol)
            log.info(f"Order cancelled: {order_id}")
            return True
        
        except ccxt.OrderNotFound as e:
            raise OrderNotFoundError(f"Order {order_id} not found")
        except ccxt.AuthenticationError as e:
            raise AuthenticationError(f"Authentication failed: {e}")
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(f"Rate limit exceeded: {e}")
        except ccxt.NetworkError as e:
            raise ConnectionError(f"Network error: {e}")
        except ccxt.ExchangeError as e:
            raise ExchangeError(f"Exchange error: {e}")
    
    def cancel_all_orders(self, symbol: Optional[str] = None) -> int:
        """Cancel all open orders."""
        if not self.api_key:
            raise AuthenticationError("API key required")
        
        try:
            open_orders = self.get_open_orders(symbol)
            cancelled = 0
            
            for order in open_orders:
                try:
                    self.cancel_order(order.symbol, order.order_id)
                    cancelled += 1
                except OrderNotFoundError:
                    pass  # Already cancelled
            
            log.info(f"Cancelled {cancelled} orders")
            return cancelled
        
        except Exception as e:
            raise ExchangeError(f"Failed to cancel orders: {e}")
    
    # =========================================================================
    # SYMBOL INFO
    # =========================================================================
    
    def get_symbols(self, quote_asset: str = 'USDT') -> List[str]:
        """Get all trading symbols."""
        try:
            self._load_markets()
            
            symbols = []
            for symbol, market in self._client.markets.items():
                if market.get('quote') == quote_asset and market.get('active'):
                    symbols.append(symbol)
            
            return sorted(symbols)
        
        except ccxt.NetworkError as e:
            raise ConnectionError(f"Network error: {e}")
        except ccxt.ExchangeError as e:
            raise ExchangeError(f"Exchange error: {e}")
    
    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        """Get symbol trading info."""
        try:
            self._load_markets()
            
            if symbol not in self._client.markets:
                raise ExchangeError(f"Symbol {symbol} not found")
            
            market = self._client.markets[symbol]
            limits = market.get('limits', {})
            precision = market.get('precision', {})
            
            # Get filters from info
            info = market.get('info', {})
            filters = {f['filterType']: f for f in info.get('filters', [])}
            
            lot_size = filters.get('LOT_SIZE', {})
            price_filter = filters.get('PRICE_FILTER', {})
            notional = filters.get('NOTIONAL', filters.get('MIN_NOTIONAL', {}))
            
            return SymbolInfo(
                symbol=symbol,
                base_asset=market.get('base', ''),
                quote_asset=market.get('quote', ''),
                status='TRADING' if market.get('active') else 'HALT',
                price_precision=precision.get('price', 8),
                quantity_precision=precision.get('amount', 8),
                min_quantity=float(lot_size.get('minQty', limits.get('amount', {}).get('min', 0))),
                max_quantity=float(lot_size.get('maxQty', limits.get('amount', {}).get('max', 1000000))),
                step_size=float(lot_size.get('stepSize', 0.00000001)),
                min_notional=float(notional.get('minNotional', 10)),
                tick_size=float(price_filter.get('tickSize', 0.00000001))
            )
        
        except ccxt.NetworkError as e:
            raise ConnectionError(f"Network error: {e}")
        except ccxt.ExchangeError as e:
            raise ExchangeError(f"Exchange error: {e}")
    
    def _load_markets(self):
        """Load markets if not cached."""
        if not self._client.markets:
            self._retry(self._client.load_markets)
    
    # =========================================================================
    # CONNECTION TEST
    # =========================================================================
    
    def test_connection(self) -> bool:
        """Test exchange connection."""
        try:
            self._retry(self._client.fetch_time)
            return True
        except Exception as e:
            log.error(f"Connection test failed: {e}")
            return False
    
    def test_authentication(self) -> bool:
        """Test API key authentication."""
        if not self.api_key:
            return False
        
        try:
            self.get_balance('USDT')
            return True
        except AuthenticationError:
            return False
        except Exception as e:
            log.error(f"Auth test failed: {e}")
            return False
    
    # =========================================================================
    # HELPERS
    # =========================================================================
    
    def _parse_order(self, data: dict) -> Order:
        """Parse order data from ccxt format."""
        status_map = {
            'open': OrderStatus.NEW,
            'closed': OrderStatus.FILLED,
            'canceled': OrderStatus.CANCELED,
            'expired': OrderStatus.EXPIRED,
            'rejected': OrderStatus.REJECTED,
        }
        
        side_map = {
            'buy': OrderSide.BUY,
            'sell': OrderSide.SELL,
        }
        
        type_map = {
            'market': OrderType.MARKET,
            'limit': OrderType.LIMIT,
            'stop_loss': OrderType.STOP_LOSS,
            'stop_loss_limit': OrderType.STOP_LOSS_LIMIT,
            'take_profit': OrderType.TAKE_PROFIT,
            'take_profit_limit': OrderType.TAKE_PROFIT_LIMIT,
        }
        
        return Order(
            order_id=str(data.get('id', '')),
            symbol=data.get('symbol', ''),
            side=side_map.get(data.get('side', ''), OrderSide.BUY),
            order_type=type_map.get(data.get('type', ''), OrderType.MARKET),
            status=status_map.get(data.get('status', ''), OrderStatus.UNKNOWN),
            price=float(data.get('price', 0) or 0),
            quantity=float(data.get('amount', 0) or 0),
            filled_quantity=float(data.get('filled', 0) or 0),
            average_price=float(data.get('average', 0) or 0),
            created_at=datetime.fromtimestamp(data.get('timestamp', 0) / 1000) if data.get('timestamp') else datetime.now(),
            updated_at=datetime.fromtimestamp(data.get('lastTradeTimestamp', 0) / 1000) if data.get('lastTradeTimestamp') else None
        )