"""
Technical indicators with caching.

Supports:
- CORE: EMA, RSI, ADX, ATR, Volume
- BONUS: MACD, Bollinger Bands, Stochastic

All indicators are optional and configurable.
"""

import pandas as pd
import pandas_ta as ta
import numpy as np
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
import hashlib
import time


# =============================================================================
# INDICATOR PARAMETERS (hardcoded for consistency)
# =============================================================================

# EMA periods
EMA_FAST = 9
EMA_MID = 21
EMA_SLOW = 50

# Oscillators
RSI_PERIOD = 14
ADX_PERIOD = 14
ATR_PERIOD = 14

# Bonus indicators
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

BB_PERIOD = 20
BB_STD = 2

STOCH_K = 14
STOCH_D = 3


# =============================================================================
# INDICATOR RESULT
# =============================================================================

@dataclass
class IndicatorValues:
    """Container for all indicator values at a specific point."""
    
    # Price
    close: float
    high: float
    low: float
    
    # EMAs
    ema9: float
    ema21: float
    ema50: float
    
    # Oscillators
    rsi: float
    adx: float
    di_plus: float
    di_minus: float
    atr: float
    atr_pct: float
    
    # Volume
    volume: float
    avg_volume: float
    volume_ratio: float
    
    # Bonus (optional)
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_histogram: Optional[float] = None
    
    bb_upper: Optional[float] = None
    bb_middle: Optional[float] = None
    bb_lower: Optional[float] = None
    bb_width_pct: Optional[float] = None
    
    stoch_k: Optional[float] = None
    stoch_d: Optional[float] = None
    
    def to_dict(self) -> dict:
        """Convert to dict (for logging/display)."""
        result = {
            'price': round(self.close, 8),
            'ema9': round(self.ema9, 8),
            'ema21': round(self.ema21, 8),
            'ema50': round(self.ema50, 8),
            'rsi': round(self.rsi, 2),
            'adx': round(self.adx, 2),
            'di_plus': round(self.di_plus, 2),
            'di_minus': round(self.di_minus, 2),
            'atr_pct': round(self.atr_pct, 3),
            'volume_ratio': round(self.volume_ratio, 2),
        }
        
        # Add bonus if present
        if self.macd is not None:
            result['macd'] = round(self.macd, 8)
            result['macd_signal'] = round(self.macd_signal, 8)
            result['macd_histogram'] = round(self.macd_histogram, 8)
        
        if self.bb_upper is not None:
            result['bb_upper'] = round(self.bb_upper, 8)
            result['bb_middle'] = round(self.bb_middle, 8)
            result['bb_lower'] = round(self.bb_lower, 8)
            result['bb_width_pct'] = round(self.bb_width_pct, 3)
        
        if self.stoch_k is not None:
            result['stoch_k'] = round(self.stoch_k, 2)
            result['stoch_d'] = round(self.stoch_d, 2)
        
        return result


# =============================================================================
# INDICATOR CALCULATOR
# =============================================================================

class IndicatorCalculator:
    """
    Calculate technical indicators with caching.
    
    Usage:
        calc = IndicatorCalculator(enable_bonus=True)
        df = calc.calculate(ohlcv_df)
        values = calc.get_latest(df)
    """
    
    def __init__(
        self,
        enable_macd: bool = True,
        enable_bb: bool = True,
        enable_stoch: bool = True,
        cache_enabled: bool = True
    ):
        """
        Initialize calculator.
        
        Args:
            enable_macd: Calculate MACD
            enable_bb: Calculate Bollinger Bands
            enable_stoch: Calculate Stochastic
            cache_enabled: Enable caching
        """
        self.enable_macd = enable_macd
        self.enable_bb = enable_bb
        self.enable_stoch = enable_stoch
        self.cache_enabled = cache_enabled
        
        # Cache
        self._cache: Dict[str, Tuple[pd.DataFrame, str, float]] = {}
        self._cache_ttl = 60  # seconds
    
    def calculate(self, df: pd.DataFrame, symbol: str = "") -> pd.DataFrame:
        """
        Calculate all indicators on dataframe.
        
        Args:
            df: OHLCV dataframe (columns: open, high, low, close, volume)
            symbol: Symbol name (for caching)
        
        Returns:
            DataFrame with indicator columns added
        """
        # Check cache
        if self.cache_enabled and symbol:
            cached = self._get_from_cache(df, symbol)
            if cached is not None:
                return cached
        
        # Make a copy to avoid modifying original
        df = df.copy()
        
        # Ensure numeric types
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Remove any NaN rows
        df = df.dropna(subset=['close'])
        
        if len(df) < 50:
            raise ValueError(f"Insufficient data: {len(df)} candles (need 50+)")
        
        # =================================================================
        # CORE INDICATORS
        # =================================================================
        
        # EMAs
        df['ema9'] = ta.ema(df['close'], length=EMA_FAST)
        df['ema21'] = ta.ema(df['close'], length=EMA_MID)
        df['ema50'] = ta.ema(df['close'], length=EMA_SLOW)
        
        # RSI
        df['rsi'] = ta.rsi(df['close'], length=RSI_PERIOD)
        
        # ADX + DI
        adx_data = ta.adx(df['high'], df['low'], df['close'], length=ADX_PERIOD)
        if adx_data is not None:
            df['adx'] = adx_data[f'ADX_{ADX_PERIOD}']
            df['di_plus'] = adx_data[f'DMP_{ADX_PERIOD}']
            df['di_minus'] = adx_data[f'DMN_{ADX_PERIOD}']
        else:
            df['adx'] = 20.0
            df['di_plus'] = 0.0
            df['di_minus'] = 0.0
        
        # ATR
        atr_data = ta.atr(df['high'], df['low'], df['close'], length=ATR_PERIOD)
        if atr_data is not None:
            df['atr'] = atr_data
        else:
            df['atr'] = df['close'] * 0.01  # Fallback: 1% of price
        
        df['atr_pct'] = (df['atr'] / df['close']) * 100
        
        # Volume ratio
        df['avg_volume'] = df['volume'].rolling(window=20, min_periods=1).mean()
        df['volume_ratio'] = df['volume'] / df['avg_volume'].replace(0, 1)
        
        # =================================================================
        # BONUS INDICATORS
        # =================================================================
        
        # MACD
        if self.enable_macd:
            macd_data = ta.macd(
                df['close'],
                fast=MACD_FAST,
                slow=MACD_SLOW,
                signal=MACD_SIGNAL
            )
            if macd_data is not None:
                df['macd'] = macd_data[f'MACD_{MACD_FAST}_{MACD_SLOW}_{MACD_SIGNAL}']
                df['macd_signal'] = macd_data[f'MACDs_{MACD_FAST}_{MACD_SLOW}_{MACD_SIGNAL}']
                df['macd_histogram'] = macd_data[f'MACDh_{MACD_FAST}_{MACD_SLOW}_{MACD_SIGNAL}']
        
        # Bollinger Bands
        if self.enable_bb:
            bb_data = ta.bbands(df['close'], length=BB_PERIOD, std=BB_STD)
            if bb_data is not None and not bb_data.empty:
                # Dynamically find column names (they may have different formats)
                bb_cols = bb_data.columns.tolist()
                
                # Find by prefix
                bb_lower_cols = [c for c in bb_cols if c.startswith('BBL_')]
                bb_middle_cols = [c for c in bb_cols if c.startswith('BBM_')]
                bb_upper_cols = [c for c in bb_cols if c.startswith('BBU_')]
                
                if bb_lower_cols and bb_middle_cols and bb_upper_cols:
                    df['bb_lower'] = bb_data[bb_lower_cols[0]]
                    df['bb_middle'] = bb_data[bb_middle_cols[0]]
                    df['bb_upper'] = bb_data[bb_upper_cols[0]]
                    df['bb_width'] = df['bb_upper'] - df['bb_lower']
                    df['bb_width_pct'] = (df['bb_width'] / df['bb_middle']) * 100
        
        # Stochastic
        if self.enable_stoch:
            stoch_data = ta.stoch(
                df['high'],
                df['low'],
                df['close'],
                k=STOCH_K,
                d=STOCH_D
            )
            if stoch_data is not None:
                df['stoch_k'] = stoch_data[f'STOCHk_{STOCH_K}_{STOCH_D}_3']
                df['stoch_d'] = stoch_data[f'STOCHd_{STOCH_K}_{STOCH_D}_3']
        
        # Cache result
        if self.cache_enabled and symbol:
            self._save_to_cache(df, symbol)
        
        return df
    
    def get_latest(self, df: pd.DataFrame) -> IndicatorValues:
        """
        Extract latest indicator values from calculated dataframe.
        
        Args:
            df: DataFrame with indicators (from calculate())
        
        Returns:
            IndicatorValues instance
        """
        if len(df) == 0:
            raise ValueError("Empty dataframe")
        
        last = df.iloc[-1]
        
        # Helper to get value or default
        def get_val(key: str, default=0.0) -> float:
            val = last.get(key, default)
            return float(val) if not pd.isna(val) else default
        
        values = IndicatorValues(
            close=get_val('close'),
            high=get_val('high'),
            low=get_val('low'),
            
            ema9=get_val('ema9', last['close']),
            ema21=get_val('ema21', last['close']),
            ema50=get_val('ema50', last['close']),
            
            rsi=get_val('rsi', 50.0),
            adx=get_val('adx', 20.0),
            di_plus=get_val('di_plus', 0.0),
            di_minus=get_val('di_minus', 0.0),
            atr=get_val('atr', last['close'] * 0.01),
            atr_pct=get_val('atr_pct', 1.0),
            
            volume=get_val('volume'),
            avg_volume=get_val('avg_volume', last['volume']),
            volume_ratio=get_val('volume_ratio', 1.0),
        )
        
        # Bonus indicators
        if 'macd' in df.columns:
            values.macd = get_val('macd')
            values.macd_signal = get_val('macd_signal')
            values.macd_histogram = get_val('macd_histogram')
        
        if 'bb_upper' in df.columns:
            values.bb_upper = get_val('bb_upper')
            values.bb_middle = get_val('bb_middle')
            values.bb_lower = get_val('bb_lower')
            values.bb_width_pct = get_val('bb_width_pct')
        
        if 'stoch_k' in df.columns:
            values.stoch_k = get_val('stoch_k')
            values.stoch_d = get_val('stoch_d')
        
        return values
    
    # =========================================================================
    # CACHE MANAGEMENT
    # =========================================================================
    
    def _get_data_hash(self, df: pd.DataFrame) -> str:
        """Generate hash of dataframe for cache key."""
        # Use last timestamp + last close as simple hash
        if len(df) == 0:
            return ""
        
        last_ts = df.index[-1] if hasattr(df.index[-1], 'timestamp') else str(df.index[-1])
        last_close = df['close'].iloc[-1]
        
        return hashlib.md5(f"{last_ts}_{last_close}".encode()).hexdigest()
    
    def _get_from_cache(self, df: pd.DataFrame, symbol: str) -> Optional[pd.DataFrame]:
        """Try to get from cache."""
        cache_key = f"{symbol}"
        
        if cache_key not in self._cache:
            return None
        
        cached_df, cached_hash, cached_time = self._cache[cache_key]
        
        # Check TTL
        if time.time() - cached_time > self._cache_ttl:
            del self._cache[cache_key]
            return None
        
        # Check if data changed
        current_hash = self._get_data_hash(df)
        if current_hash != cached_hash:
            return None
        
        return cached_df
    
    def _save_to_cache(self, df: pd.DataFrame, symbol: str):
        """Save to cache."""
        cache_key = f"{symbol}"
        data_hash = self._get_data_hash(df)
        
        self._cache[cache_key] = (df.copy(), data_hash, time.time())
        
        # Limit cache size (prevent memory leak)
        if len(self._cache) > 100:
            # Remove oldest
            oldest = min(self._cache.items(), key=lambda x: x[1][2])
            del self._cache[oldest[0]]
    
    def clear_cache(self):
        """Clear all cached data."""
        self._cache.clear()