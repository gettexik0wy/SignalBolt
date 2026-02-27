"""
Data manager for backtesting.

Downloads historical OHLCV data from Binance Vision (free).
"""

import os
import time
import requests
import zipfile
import io
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.backtest.data_manager")


# =============================================================================
# PATHS
# =============================================================================

ROOT_DIR = Path(__file__).parent.parent.parent
DATA_DIR = ROOT_DIR / "market_data"
PARQUET_DIR = DATA_DIR / "parquet"
CSV_CACHE_DIR = DATA_DIR / "csv_cache"

PARQUET_DIR.mkdir(parents=True, exist_ok=True)
CSV_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# =============================================================================
# DATA MANAGER
# =============================================================================


class DataManager:
    """
    Download and cache historical data from Binance Vision.

    Free, no API key required, perfect for backtesting.

    Usage:
        manager = DataManager()
        df = manager.get_data('BTCUSDT', '5m', '2022-01-01', '2022-12-31')
    """

    BASE_URL = "https://data.binance.vision/data/futures/um"

    def __init__(self, verbose: bool = True):
        """
        Initialize data manager.

        Args:
            verbose: Print download progress
        """
        self.session = requests.Session()
        self.verbose = verbose

    def _log(self, msg: str):
        """Log message if verbose."""
        if self.verbose:
            print(f"  {msg}")

    def get_data(
        self,
        symbol: str,
        interval: str,
        start_date: str,
        end_date: str,
        force_download: bool = False,
    ) -> Optional[pd.DataFrame]:
        """
        Get historical OHLCV data.

        Args:
            symbol: Trading symbol (e.g., 'BTCUSDT')
            interval: Candle interval (e.g., '5m')
            start_date: Start date YYYY-MM-DD
            end_date: End date YYYY-MM-DD
            force_download: Force re-download even if cached

        Returns:
            DataFrame with columns: timestamp (index), open, high, low, close, volume
        """
        clean_symbol = symbol.replace("/", "").replace(":", "").upper()
        date_suffix = f"{start_date}_{end_date}".replace("-", "")
        parquet_path = PARQUET_DIR / f"{clean_symbol}_{interval}_{date_suffix}.parquet"

        # Check cache
        if parquet_path.exists() and not force_download:
            try:
                df = pd.read_parquet(parquet_path)
                self._log(f"📁 Loaded from cache: {len(df):,} candles")
                return df
            except Exception as e:
                log.warning(f"Cache read failed: {e}")

        # Download
        self._log(
            f"📥 Downloading {clean_symbol} [{interval}] {start_date} → {end_date}"
        )

        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")

        all_dfs = []
        current = start_dt.replace(day=1)

        total_months = (
            (end_dt.year - start_dt.year) * 12 + end_dt.month - start_dt.month
        ) + 1
        month_num = 0

        while current <= end_dt:
            month_num += 1
            progress = (month_num / total_months) * 100

            ym = current.strftime("%Y-%m")
            print(f"\r  📥 Downloading... {progress:.0f}% ({ym})", end="", flush=True)

            # Try monthly file first
            df_month = self._download_month(clean_symbol, interval, ym)

            if df_month is not None:
                all_dfs.append(df_month)
            else:
                # Fallback to daily files
                daily_dfs = self._download_daily_range(
                    clean_symbol, interval, current, end_dt
                )
                all_dfs.extend(daily_dfs)

            # Next month
            current = (current.replace(day=1) + timedelta(days=32)).replace(day=1)

        print()  # New line after progress

        if not all_dfs:
            self._log(f"❌ No data found for {clean_symbol}")
            return None

        # Combine and clean
        final_df = pd.concat(all_dfs, ignore_index=True)
        final_df = self._sanitize(final_df, start_dt, end_dt)

        # Save cache
        final_df.to_parquet(parquet_path, compression="snappy")
        self._log(f"💾 Cached {len(final_df):,} candles")

        return final_df

    def _download_month(
        self, symbol: str, interval: str, year_month: str
    ) -> Optional[pd.DataFrame]:
        """Download monthly ZIP file."""
        filename = f"{symbol}-{interval}-{year_month}"
        url = f"{self.BASE_URL}/monthly/klines/{symbol}/{interval}/{filename}.zip"

        # Check local cache
        local_csv = CSV_CACHE_DIR / f"{filename}.csv"
        if local_csv.exists():
            return self._load_csv(local_csv)

        try:
            r = self.session.get(url, timeout=30)
            if r.status_code == 200:
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    csv_content = z.read(z.namelist()[0])
                    with open(local_csv, "wb") as f:
                        f.write(csv_content)
                    return self._load_csv(local_csv)
        except Exception as e:
            log.debug(f"Failed to download {year_month}: {e}")

        return None

    def _download_daily_range(
        self, symbol: str, interval: str, start_month: datetime, end_date: datetime
    ) -> list:
        """Download daily files for a month."""
        daily_dfs = []

        for d in range(1, 32):
            try:
                day_date = start_month.replace(day=d)
            except ValueError:
                continue

            if day_date > end_date or day_date >= datetime.now():
                break

            filename = f"{symbol}-{interval}-{day_date.strftime('%Y-%m-%d')}"
            url = f"{self.BASE_URL}/daily/klines/{symbol}/{interval}/{filename}.zip"

            local_csv = CSV_CACHE_DIR / f"{filename}.csv"

            if local_csv.exists():
                df = self._load_csv(local_csv)
                if df is not None:
                    daily_dfs.append(df)
                continue

            try:
                r = self.session.get(url, timeout=15)
                if r.status_code == 200:
                    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                        csv_content = z.read(z.namelist()[0])
                        with open(local_csv, "wb") as f:
                            f.write(csv_content)
                        df = self._load_csv(local_csv)
                        if df is not None:
                            daily_dfs.append(df)
            except:
                pass

            time.sleep(0.05)  # Rate limit

        return daily_dfs

    def _load_csv(self, path: Path) -> Optional[pd.DataFrame]:
        """Load CSV file."""
        try:
            df = pd.read_csv(path, header=None, low_memory=False)

            # Skip header row if exists
            if not str(df.iloc[0, 0]).replace(".", "").isdigit():
                df = df.iloc[1:]

            # Take first 6 columns (timestamp, OHLCV)
            return df.iloc[:, :6]
        except Exception as e:
            log.debug(f"Failed to load {path}: {e}")
            return None

    def _sanitize(
        self, df: pd.DataFrame, start_dt: datetime, end_dt: datetime
    ) -> pd.DataFrame:
        """Clean and format dataframe."""
        df.columns = ["timestamp", "open", "high", "low", "close", "volume"]

        # Convert timestamp
        df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
        df = df.dropna(subset=["timestamp"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")

        # Convert OHLCV to float
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype(np.float32)

        # Filter date range
        mask = (df["timestamp"] >= start_dt) & (
            df["timestamp"] <= end_dt + timedelta(days=1)
        )
        df = df[mask]

        # Sort and dedupe
        df = df.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
        df = df.dropna(subset=["open", "close"])
        df = df.set_index("timestamp")

        return df

    def clear_cache(self, symbol: Optional[str] = None):
        """
        Clear cached data.

        Args:
            symbol: Specific symbol to clear (None = all)
        """
        if symbol:
            pattern = f"{symbol.upper()}_*"
            for f in PARQUET_DIR.glob(pattern):
                f.unlink()
                log.info(f"Removed cache: {f.name}")
        else:
            for f in PARQUET_DIR.glob("*.parquet"):
                f.unlink()
            log.info("All cache cleared")
