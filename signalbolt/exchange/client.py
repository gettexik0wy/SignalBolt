"""
Exchange client factory and wrapper.

Provides:
- Easy exchange instantiation
- Environment variable loading
- Singleton pattern for reuse
"""

import os
from typing import Optional
from dotenv import load_dotenv

from signalbolt.exchange.base import ExchangeBase
from signalbolt.exchange.binance import BinanceExchange
from signalbolt.utils.logger import get_logger

# Load .env
load_dotenv()

log = get_logger('signalbolt.exchange.client')

# Singleton instance
_exchange_instance: Optional[ExchangeBase] = None


def get_exchange(
    exchange_name: str = 'binance',
    testnet: bool = False,
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None
) -> ExchangeBase:
    """
    Get exchange instance.
    
    Args:
        exchange_name: 'binance' (more coming soon)
        testnet: Use testnet
        api_key: API key (or use env var)
        api_secret: API secret (or use env var)
    
    Returns:
        ExchangeBase instance
    
    Usage:
        # From environment variables
        exchange = get_exchange('binance')
        
        # With explicit keys
        exchange = get_exchange('binance', api_key='xxx', api_secret='yyy')
        
        # Testnet
        exchange = get_exchange('binance', testnet=True)
    """
    global _exchange_instance
    
    # Get API keys from env if not provided
    if api_key is None:
        if exchange_name == 'binance':
            if testnet:
                api_key = os.getenv('BINANCE_TESTNET_API_KEY')
                api_secret = os.getenv('BINANCE_TESTNET_API_SECRET')
            else:
                api_key = os.getenv('BINANCE_API_KEY')
                api_secret = api_secret or os.getenv('BINANCE_API_SECRET')
    
    # Create exchange
    if exchange_name.lower() == 'binance':
        exchange = BinanceExchange(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet
        )
    else:
        raise ValueError(f"Unknown exchange: {exchange_name}")
    
    log.info(f"Exchange client created: {exchange.name}")
    return exchange


def get_default_exchange() -> ExchangeBase:
    """
    Get default exchange (singleton).
    
    Reads from environment:
        - EXCHANGE_NAME (default: 'binance')
        - EXCHANGE_TESTNET (default: 'false')
    
    Returns:
        ExchangeBase instance
    """
    global _exchange_instance
    
    if _exchange_instance is None:
        exchange_name = os.getenv('EXCHANGE_NAME', 'binance')
        testnet = os.getenv('EXCHANGE_TESTNET', 'false').lower() == 'true'
        
        _exchange_instance = get_exchange(exchange_name, testnet)
    
    return _exchange_instance


def reset_exchange():
    """Reset singleton (for testing)."""
    global _exchange_instance
    _exchange_instance = None