"""
Coin discovery system.

Features:
- Multiple discovery modes (volume, momentum, regime-aware)
- Configurable filters (volume, spread, blacklist)
- Automatic refresh
- Regime-adaptive selection
- User whitelist/blacklist support

Usage:
    discovery = CoinDiscovery(exchange, config)
    
    # Get top coins by volume
    coins = discovery.get_top_coins(30)
    
    # Get coins for current regime
    coins = discovery.get_regime_adapted(regime='bear')
    
    # Custom filters
    coins = discovery.discover(
        mode='momentum',
        count=20,
        min_volume=10_000_000,
        min_change_24h=2.0
    )
"""

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Set, Callable
from dataclasses import dataclass, field
from enum import Enum
import random

from signalbolt.exchange.base import ExchangeBase, Ticker
from signalbolt.data.price_feed import PriceFeed
from signalbolt.data.liquidity import LiquidityAnalyzer, LiquidityTier
from signalbolt.core.config import Config
from signalbolt.utils.logger import get_logger

log = get_logger('signalbolt.signals.discovery')


# =============================================================================
# DISCOVERY MODES
# =============================================================================

class DiscoveryMode(Enum):
    """Coin selection strategy."""
    VOLUME = "volume"              # Top by 24h volume
    MOMENTUM = "momentum"          # Positive momentum + volume
    VOLATILITY = "volatility"      # High ATR for scalping
    REGIME_AWARE = "regime_aware"  # Adapts to market regime
    MIXED = "mixed"                # Combination of factors
    WHITELIST = "whitelist"        # User-defined list only


# =============================================================================
# COIN INFO
# =============================================================================

@dataclass
class CoinInfo:
    """Discovered coin information."""
    
    symbol: str
    
    # Market data
    price: float
    volume_24h: float
    change_24h_pct: float
    spread_pct: float
    
    # Rankings
    volume_rank: int
    momentum_rank: int
    
    # Scores
    discovery_score: float  # 0-100
    
    # Metadata
    discovery_mode: str
    discovered_at: datetime
    
    # Liquidity
    liquidity_tier: str = "unknown"
    is_tradeable: bool = True
    
    def to_dict(self) -> dict:
        return {
            'symbol': self.symbol,
            'price': round(self.price, 8),
            'volume_24h': round(self.volume_24h, 2),
            'change_24h_pct': round(self.change_24h_pct, 2),
            'spread_pct': round(self.spread_pct, 4),
            'volume_rank': self.volume_rank,
            'momentum_rank': self.momentum_rank,
            'discovery_score': round(self.discovery_score, 1),
            'liquidity_tier': self.liquidity_tier,
            'is_tradeable': self.is_tradeable,
        }


@dataclass
class DiscoveryResult:
    """Result of discovery operation."""
    
    mode: DiscoveryMode
    coins: List[CoinInfo]
    
    # Stats
    total_scanned: int
    total_passed: int
    
    # Filters applied
    min_volume: float
    max_spread: float
    
    # Timing
    started_at: datetime
    completed_at: datetime
    
    @property
    def duration_sec(self) -> float:
        return (self.completed_at - self.started_at).total_seconds()
    
    @property
    def symbols(self) -> List[str]:
        """Get list of symbols."""
        return [c.symbol for c in self.coins]
    
    def to_dict(self) -> dict:
        return {
            'mode': self.mode.value,
            'coin_count': len(self.coins),
            'total_scanned': self.total_scanned,
            'total_passed': self.total_passed,
            'duration_sec': round(self.duration_sec, 2),
            'coins': [c.to_dict() for c in self.coins],
        }


# =============================================================================
# DISCOVERY CONFIG
# =============================================================================

@dataclass
class DiscoveryConfig:
    """Discovery configuration."""
    
    # Defaults
    default_count: int = 30
    default_mode: DiscoveryMode = DiscoveryMode.VOLUME
    
    # Filters
    min_volume_24h: float = 5_000_000
    max_spread_pct: float = 0.3
    
    # Momentum mode
    min_change_24h: float = 0.0      # For momentum mode
    max_change_24h: float = 50.0     # Avoid pump & dumps
    
    # Refresh
    refresh_interval_hours: float = 4.0
    
    # Lists
    blacklist: Set[str] = field(default_factory=set)
    whitelist: Set[str] = field(default_factory=set)
    
    # Stablecoins to exclude
    exclude_stables: bool = True
    stablecoins: Set[str] = field(default_factory=lambda: {
        'USDT', 'USDC', 'BUSD', 'DAI', 'TUSD', 'USDP', 
        'FDUSD', 'USDD', 'GUSD', 'FRAX', 'LUSD', 'SUSD'
    })
    
    # Quote asset
    quote_asset: str = 'USDT'


# =============================================================================
# COIN DISCOVERY
# =============================================================================

class CoinDiscovery:
    """
    Discover tradeable coins based on various criteria.
    
    Usage:
        discovery = CoinDiscovery(exchange, config)
        
        # Simple: top by volume
        coins = discovery.get_top_coins(30)
        
        # Momentum coins
        coins = discovery.get_momentum_coins(20)
        
        # Regime-aware
        coins = discovery.get_regime_adapted('bear')
        
        # Full control
        result = discovery.discover(
            mode=DiscoveryMode.MIXED,
            count=50,
            min_volume=10_000_000
        )
    """
    
    def __init__(
        self,
        exchange: ExchangeBase,
        config: Optional[Config] = None,
        discovery_config: Optional[DiscoveryConfig] = None
    ):
        """
        Initialize discovery.
        
        Args:
            exchange: Exchange instance
            config: SignalBolt config
            discovery_config: Discovery-specific config
        """
        self.exchange = exchange
        self.config = config
        self.disc_config = discovery_config or self._load_config(config)
        
        # Components
        self.price_feed = PriceFeed(exchange)
        self.liquidity = LiquidityAnalyzer(exchange, self.price_feed)
        
        # Cache
        self._cache: Optional[DiscoveryResult] = None
        self._last_refresh: Optional[datetime] = None
        
        log.info(f"CoinDiscovery initialized (quote: {self.disc_config.quote_asset})")
    
    def _load_config(self, config: Optional[Config]) -> DiscoveryConfig:
        """Load config from SignalBolt config."""
        if not config:
            return DiscoveryConfig()
        
        return DiscoveryConfig(
            default_count=config.get('discovery', 'top_coins', default=30),
            min_volume_24h=config.get('discovery', 'min_volume_24h', default=5_000_000),
            max_spread_pct=config.get('discovery', 'max_spread_pct', default=0.3),
            refresh_interval_hours=config.get('discovery', 'refresh_hours', default=4.0),
            blacklist=set(config.get('filters', 'blacklist', default=[])),
            whitelist=set(config.get('filters', 'whitelist', default=[])),
        )
    
    # =========================================================================
    # SIMPLE DISCOVERY METHODS
    # =========================================================================
    
    def get_top_coins(
        self,
        count: Optional[int] = None,
        use_cache: bool = True
    ) -> List[str]:
        """
        Get top coins by 24h volume.
        
        Args:
            count: Number of coins (default from config)
            use_cache: Use cached result if fresh
        
        Returns:
            List of symbol names
        """
        count = count or self.disc_config.default_count
        
        result = self.discover(
            mode=DiscoveryMode.VOLUME,
            count=count,
            use_cache=use_cache
        )
        
        return result.symbols
    
    def get_momentum_coins(
        self,
        count: int = 20,
        min_change: float = 2.0
    ) -> List[str]:
        """
        Get coins with positive momentum.
        
        Args:
            count: Number of coins
            min_change: Minimum 24h change %
        
        Returns:
            List of symbols
        """
        result = self.discover(
            mode=DiscoveryMode.MOMENTUM,
            count=count,
            min_change_24h=min_change
        )
        
        return result.symbols
    
    def get_volatile_coins(
        self,
        count: int = 20,
        min_change: float = 3.0
    ) -> List[str]:
        """
        Get highly volatile coins (for scalping).
        
        Args:
            count: Number of coins
            min_change: Minimum absolute 24h change
        
        Returns:
            List of symbols
        """
        result = self.discover(
            mode=DiscoveryMode.VOLATILITY,
            count=count,
            min_change_24h=min_change
        )
        
        return result.symbols
    
    def get_regime_adapted(
        self,
        regime: str,
        count: Optional[int] = None
    ) -> List[str]:
        """
        Get coins adapted to current market regime.
        
        Args:
            regime: 'bull', 'bear', 'range', 'crash'
            count: Number of coins
        
        Returns:
            List of symbols
        """
        count = count or self.disc_config.default_count
        
        result = self.discover(
            mode=DiscoveryMode.REGIME_AWARE,
            count=count,
            regime=regime
        )
        
        return result.symbols
    
    def get_whitelist_coins(self) -> List[str]:
        """Get user-defined whitelist coins."""
        if not self.disc_config.whitelist:
            log.warning("Whitelist is empty")
            return []
        
        # Validate whitelist coins exist
        valid = []
        
        for symbol in self.disc_config.whitelist:
            # Ensure proper format
            if not symbol.endswith(self.disc_config.quote_asset):
                symbol = f"{symbol}{self.disc_config.quote_asset}"
            
            if self.liquidity.is_tradeable(symbol):
                valid.append(symbol)
            else:
                log.warning(f"Whitelist coin not tradeable: {symbol}")
        
        return valid
    
    # =========================================================================
    # MAIN DISCOVERY METHOD
    # =========================================================================
    
    def discover(
        self,
        mode: DiscoveryMode = DiscoveryMode.VOLUME,
        count: Optional[int] = None,
        min_volume: Optional[float] = None,
        max_spread: Optional[float] = None,
        min_change_24h: Optional[float] = None,
        max_change_24h: Optional[float] = None,
        regime: Optional[str] = None,
        use_cache: bool = True
    ) -> DiscoveryResult:
        """
        Discover coins with full control over parameters.
        
        Args:
            mode: Discovery strategy
            count: Number of coins to return
            min_volume: Minimum 24h volume USD
            max_spread: Maximum spread %
            min_change_24h: Minimum 24h change %
            max_change_24h: Maximum 24h change %
            regime: Market regime (for REGIME_AWARE mode)
            use_cache: Use cached result
        
        Returns:
            DiscoveryResult with coins and stats
        """
        started_at = datetime.now()
        
        # Use defaults
        count = count or self.disc_config.default_count
        min_volume = min_volume or self.disc_config.min_volume_24h
        max_spread = max_spread or self.disc_config.max_spread_pct
        min_change_24h = min_change_24h if min_change_24h is not None else self.disc_config.min_change_24h
        max_change_24h = max_change_24h or self.disc_config.max_change_24h
        
        # Check cache
        if use_cache and self._is_cache_valid():
            log.debug("Using cached discovery result")
            
            # Filter cache to requested count
            cached = self._cache
            if len(cached.coins) >= count:
                return DiscoveryResult(
                    mode=mode,
                    coins=cached.coins[:count],
                    total_scanned=cached.total_scanned,
                    total_passed=cached.total_passed,
                    min_volume=min_volume,
                    max_spread=max_spread,
                    started_at=started_at,
                    completed_at=datetime.now()
                )
        
        # Whitelist mode
        if mode == DiscoveryMode.WHITELIST:
            return self._discover_whitelist(started_at)
        
        # Fetch all tickers
        log.info(f"Discovering coins (mode={mode.value}, count={count})")
        
        try:
            all_tickers = self.exchange.get_tickers()
        except Exception as e:
            log.error(f"Failed to fetch tickers: {e}")
            return DiscoveryResult(
                mode=mode,
                coins=[],
                total_scanned=0,
                total_passed=0,
                min_volume=min_volume,
                max_spread=max_spread,
                started_at=started_at,
                completed_at=datetime.now()
            )
        
        total_scanned = len(all_tickers)
        
        # Filter tickers
        filtered = self._filter_tickers(
            all_tickers,
            min_volume=min_volume,
            max_spread=max_spread,
            min_change=min_change_24h,
            max_change=max_change_24h
        )
        
        total_passed = len(filtered)
        
        # Apply discovery mode
        if mode == DiscoveryMode.VOLUME:
            scored = self._score_by_volume(filtered)
        
        elif mode == DiscoveryMode.MOMENTUM:
            scored = self._score_by_momentum(filtered)
        
        elif mode == DiscoveryMode.VOLATILITY:
            scored = self._score_by_volatility(filtered)
        
        elif mode == DiscoveryMode.REGIME_AWARE:
            scored = self._score_by_regime(filtered, regime or 'range')
        
        elif mode == DiscoveryMode.MIXED:
            scored = self._score_mixed(filtered)
        
        else:
            scored = self._score_by_volume(filtered)
        
        # Sort by score and take top N
        scored.sort(key=lambda x: x.discovery_score, reverse=True)
        top_coins = scored[:count]
        
        # Build result
        result = DiscoveryResult(
            mode=mode,
            coins=top_coins,
            total_scanned=total_scanned,
            total_passed=total_passed,
            min_volume=min_volume,
            max_spread=max_spread,
            started_at=started_at,
            completed_at=datetime.now()
        )
        
        # Update cache
        self._cache = result
        self._last_refresh = datetime.now()
        
        log.info(f"Discovered {len(top_coins)} coins from {total_passed} candidates "
                 f"(scanned {total_scanned}, {result.duration_sec:.1f}s)")
        
        return result
    
    # =========================================================================
    # FILTERING
    # =========================================================================
    
    def _filter_tickers(
        self,
        tickers: List[Ticker],
        min_volume: float,
        max_spread: float,
        min_change: float,
        max_change: float
    ) -> List[Ticker]:
        """Apply filters to ticker list."""
        filtered = []
        
        for ticker in tickers:
            symbol = ticker.symbol
            
            # Quote asset check
            if not symbol.endswith(self.disc_config.quote_asset):
                continue
            
            # Extract base asset
            base = symbol.replace(self.disc_config.quote_asset, '')
            
            # Stablecoin check
            if self.disc_config.exclude_stables:
                if base in self.disc_config.stablecoins:
                    continue
            
            # Blacklist check
            if symbol in self.disc_config.blacklist or base in self.disc_config.blacklist:
                continue
            
            # Volume check
            if ticker.volume_24h < min_volume:
                continue
            
            # Spread check
            if ticker.spread_pct > max_spread:
                continue
            
            # Change check (absolute for volatility)
            if min_change > 0:
                if abs(ticker.change_24h_pct) < min_change:
                    continue
            
            # Max change (avoid pump & dumps)
            if abs(ticker.change_24h_pct) > max_change:
                continue
            
            # Price sanity check
            if ticker.last <= 0:
                continue
            
            filtered.append(ticker)
        
        return filtered
    
    # =========================================================================
    # SCORING STRATEGIES
    # =========================================================================
    
    def _score_by_volume(self, tickers: List[Ticker]) -> List[CoinInfo]:
        """Score coins by volume (simple ranking)."""
        # Sort by volume
        sorted_tickers = sorted(tickers, key=lambda t: t.volume_24h, reverse=True)
        
        coins = []
        for rank, ticker in enumerate(sorted_tickers, 1):
            # Volume score (log scale)
            import math
            vol_score = min(100, math.log10(ticker.volume_24h) * 10)
            
            coins.append(CoinInfo(
                symbol=ticker.symbol,
                price=ticker.last,
                volume_24h=ticker.volume_24h,
                change_24h_pct=ticker.change_24h_pct,
                spread_pct=ticker.spread_pct,
                volume_rank=rank,
                momentum_rank=0,
                discovery_score=vol_score,
                discovery_mode='volume',
                discovered_at=datetime.now()
            ))
        
        return coins
    
    def _score_by_momentum(self, tickers: List[Ticker]) -> List[CoinInfo]:
        """Score coins by momentum (positive change + volume)."""
        # Filter positive change
        positive = [t for t in tickers if t.change_24h_pct > 0]
        
        # Sort by change
        sorted_tickers = sorted(positive, key=lambda t: t.change_24h_pct, reverse=True)
        
        coins = []
        for rank, ticker in enumerate(sorted_tickers, 1):
            # Momentum score: change weighted by volume
            import math
            vol_factor = min(1.0, math.log10(ticker.volume_24h / 1_000_000) / 3)
            momentum_score = ticker.change_24h_pct * (0.5 + 0.5 * vol_factor)
            
            # Cap at 100
            momentum_score = min(100, momentum_score * 5)
            
            coins.append(CoinInfo(
                symbol=ticker.symbol,
                price=ticker.last,
                volume_24h=ticker.volume_24h,
                change_24h_pct=ticker.change_24h_pct,
                spread_pct=ticker.spread_pct,
                volume_rank=0,
                momentum_rank=rank,
                discovery_score=momentum_score,
                discovery_mode='momentum',
                discovered_at=datetime.now()
            ))
        
        return coins
    
    def _score_by_volatility(self, tickers: List[Ticker]) -> List[CoinInfo]:
        """Score coins by volatility (absolute change)."""
        # Sort by absolute change
        sorted_tickers = sorted(tickers, key=lambda t: abs(t.change_24h_pct), reverse=True)
        
        coins = []
        for rank, ticker in enumerate(sorted_tickers, 1):
            # Volatility score
            import math
            vol_factor = min(1.0, math.log10(ticker.volume_24h / 1_000_000) / 3)
            volatility_score = abs(ticker.change_24h_pct) * (0.5 + 0.5 * vol_factor)
            
            # Cap at 100
            volatility_score = min(100, volatility_score * 4)
            
            coins.append(CoinInfo(
                symbol=ticker.symbol,
                price=ticker.last,
                volume_24h=ticker.volume_24h,
                change_24h_pct=ticker.change_24h_pct,
                spread_pct=ticker.spread_pct,
                volume_rank=0,
                momentum_rank=rank,
                discovery_score=volatility_score,
                discovery_mode='volatility',
                discovered_at=datetime.now()
            ))
        
        return coins
    
    def _score_by_regime(self, tickers: List[Ticker], regime: str) -> List[CoinInfo]:
        """Score coins adapted to market regime."""
        import math
        
        coins = []
        
        for ticker in tickers:
            # Base scores
            vol_score = min(50, math.log10(ticker.volume_24h / 1_000_000) * 10)
            momentum_score = ticker.change_24h_pct * 2
            spread_score = max(0, 20 - ticker.spread_pct * 100)
            
            # Regime-specific weighting
            if regime == 'bull':
                # Prefer momentum + volume
                score = vol_score * 0.4 + max(0, momentum_score) * 0.5 + spread_score * 0.1
            
            elif regime == 'bear':
                # Prefer stability + tight spreads
                score = vol_score * 0.6 + spread_score * 0.3 - abs(momentum_score) * 0.1
            
            elif regime == 'crash':
                # Only highest liquidity
                score = vol_score * 0.8 + spread_score * 0.2
            
            else:  # range
                # Balanced
                score = vol_score * 0.5 + abs(momentum_score) * 0.3 + spread_score * 0.2
            
            coins.append(CoinInfo(
                symbol=ticker.symbol,
                price=ticker.last,
                volume_24h=ticker.volume_24h,
                change_24h_pct=ticker.change_24h_pct,
                spread_pct=ticker.spread_pct,
                volume_rank=0,
                momentum_rank=0,
                discovery_score=max(0, min(100, score)),
                discovery_mode=f'regime_{regime}',
                discovered_at=datetime.now()
            ))
        
        return coins
    
    def _score_mixed(self, tickers: List[Ticker]) -> List[CoinInfo]:
        """Score coins with mixed factors."""
        import math
        
        coins = []
        
        # Calculate ranks
        vol_sorted = sorted(tickers, key=lambda t: t.volume_24h, reverse=True)
        vol_ranks = {t.symbol: i+1 for i, t in enumerate(vol_sorted)}
        
        mom_sorted = sorted(tickers, key=lambda t: t.change_24h_pct, reverse=True)
        mom_ranks = {t.symbol: i+1 for i, t in enumerate(mom_sorted)}
        
        total = len(tickers)
        
        for ticker in tickers:
            # Percentile-based scores
            vol_pct = 1 - (vol_ranks[ticker.symbol] / total)
            mom_pct = 1 - (mom_ranks[ticker.symbol] / total)
            
            # Spread penalty
            spread_penalty = min(20, ticker.spread_pct * 100)
            
            # Combined score
            score = (vol_pct * 50 + mom_pct * 30 + 20) - spread_penalty
            
            coins.append(CoinInfo(
                symbol=ticker.symbol,
                price=ticker.last,
                volume_24h=ticker.volume_24h,
                change_24h_pct=ticker.change_24h_pct,
                spread_pct=ticker.spread_pct,
                volume_rank=vol_ranks[ticker.symbol],
                momentum_rank=mom_ranks[ticker.symbol],
                discovery_score=max(0, min(100, score)),
                discovery_mode='mixed',
                discovered_at=datetime.now()
            ))
        
        return coins
    
    def _discover_whitelist(self, started_at: datetime) -> DiscoveryResult:
        """Discover from whitelist only."""
        coins = []
        
        for symbol in self.disc_config.whitelist:
            # Ensure proper format
            if not symbol.endswith(self.disc_config.quote_asset):
                symbol = f"{symbol}{self.disc_config.quote_asset}"
            
            try:
                ticker = self.price_feed.get_ticker(symbol)
                
                if ticker and ticker.last > 0:
                    coins.append(CoinInfo(
                        symbol=symbol,
                        price=ticker.last,
                        volume_24h=ticker.volume_24h,
                        change_24h_pct=ticker.change_24h_pct,
                        spread_pct=ticker.spread_pct,
                        volume_rank=0,
                        momentum_rank=0,
                        discovery_score=100,  # Whitelist = max score
                        discovery_mode='whitelist',
                        discovered_at=datetime.now()
                    ))
            except Exception as e:
                log.warning(f"Failed to get whitelist coin {symbol}: {e}")
        
        return DiscoveryResult(
            mode=DiscoveryMode.WHITELIST,
            coins=coins,
            total_scanned=len(self.disc_config.whitelist),
            total_passed=len(coins),
            min_volume=0,
            max_spread=999,
            started_at=started_at,
            completed_at=datetime.now()
        )
    
    # =========================================================================
    # CACHE MANAGEMENT
    # =========================================================================
    
    def _is_cache_valid(self) -> bool:
        """Check if cache is still valid."""
        if self._cache is None or self._last_refresh is None:
            return False
        
        age = datetime.now() - self._last_refresh
        max_age = timedelta(hours=self.disc_config.refresh_interval_hours)
        
        return age < max_age
    
    def refresh(self) -> DiscoveryResult:
        """Force refresh discovery."""
        return self.discover(use_cache=False)
    
    def clear_cache(self):
        """Clear cached results."""
        self._cache = None
        self._last_refresh = None
    
    # =========================================================================
    # LIST MANAGEMENT
    # =========================================================================
    
    def add_to_blacklist(self, symbol: str):
        """Add symbol to blacklist."""
        self.disc_config.blacklist.add(symbol.upper())
        log.info(f"Added to blacklist: {symbol}")
    
    def remove_from_blacklist(self, symbol: str):
        """Remove symbol from blacklist."""
        self.disc_config.blacklist.discard(symbol.upper())
        log.info(f"Removed from blacklist: {symbol}")
    
    def add_to_whitelist(self, symbol: str):
        """Add symbol to whitelist."""
        self.disc_config.whitelist.add(symbol.upper())
        log.info(f"Added to whitelist: {symbol}")
    
    def remove_from_whitelist(self, symbol: str):
        """Remove symbol from whitelist."""
        self.disc_config.whitelist.discard(symbol.upper())
        log.info(f"Removed from whitelist: {symbol}")
    
    def get_blacklist(self) -> List[str]:
        """Get current blacklist."""
        return list(self.disc_config.blacklist)
    
    def get_whitelist(self) -> List[str]:
        """Get current whitelist."""
        return list(self.disc_config.whitelist)
    
    # =========================================================================
    # UTILITY
    # =========================================================================
    
    def get_stats(self) -> dict:
        """Get discovery statistics."""
        return {
            'cache_valid': self._is_cache_valid(),
            'last_refresh': self._last_refresh.isoformat() if self._last_refresh else None,
            'cached_coins': len(self._cache.coins) if self._cache else 0,
            'blacklist_size': len(self.disc_config.blacklist),
            'whitelist_size': len(self.disc_config.whitelist),
            'default_count': self.disc_config.default_count,
            'min_volume': self.disc_config.min_volume_24h,
            'max_spread': self.disc_config.max_spread_pct,
        }


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def create_discovery(
    exchange: ExchangeBase,
    config: Optional[Config] = None
) -> CoinDiscovery:
    """Create coin discovery instance."""
    return CoinDiscovery(exchange, config)


def get_top_coins(
    exchange: ExchangeBase,
    count: int = 30,
    min_volume: float = 5_000_000
) -> List[str]:
    """Quick function to get top coins by volume."""
    discovery = CoinDiscovery(exchange)
    discovery.disc_config.min_volume_24h = min_volume
    
    return discovery.get_top_coins(count)