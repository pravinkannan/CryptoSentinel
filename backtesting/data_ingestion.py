"""
backtesting/data_ingestion.py - Robust Historical Market Data Ingestion

Handles loading, validating, sanitizing, and resampling OHLCV candlestick data
from local CSV files into structured, timezone-aware Pandas DataFrames.
"""

from datetime import datetime, timezone
import logging
import os
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

logger = logging.getLogger("backtester.ingestion")

# Canonical column mapping dictionary
COLUMN_ALIASES: Dict[str, List[str]] = {
    "timestamp": ["timestamp", "time", "date", "datetime", "open_time", "open time", "ts"],
    "open": ["open", "o", "open_price", "open price"],
    "high": ["high", "h", "high_price", "high price"],
    "low": ["low", "l", "low_price", "low price"],
    "close": ["close", "c", "close_price", "close price"],
    "volume": ["volume", "vol", "v", "base_volume", "base volume"],
}


class DataValidationError(ValueError):
    """Raised when historical data fails quantitative validation checks."""
    pass


def normalize_ohlcv_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Standardizes DataFrame column names to ['timestamp', 'open', 'high', 'low', 'close', 'volume'].
    Case-insensitive matching against common exchange / provider aliases.
    """
    df = df.copy()
    col_map: Dict[str, str] = {}
    lower_to_orig = {str(c).strip().lower(): c for c in df.columns}

    for canonical, aliases in COLUMN_ALIASES.items():
        matched = False
        for alias in aliases:
            if alias.lower() in lower_to_orig:
                orig_col = lower_to_orig[alias.lower()]
                col_map[orig_col] = canonical
                matched = True
                break
        if not matched and canonical != "volume":
            raise DataValidationError(
                f"Missing required OHLCV column '{canonical}'. Available columns: {list(df.columns)}"
            )

    df = df.rename(columns=col_map)

    # Volume fallback if not present
    if "volume" not in df.columns:
        logger.warning("Volume column missing from CSV; defaulting to 1000.0.")
        df["volume"] = 1000.0

    return df[["timestamp", "open", "high", "low", "close", "volume"]]


def parse_timestamps(series: pd.Series) -> pd.Series:
    """
    Parses timestamps from integer/float (unix ms or s) or strings into UTC tz-aware datetime series.
    """
    if pd.api.types.is_numeric_dtype(series):
        # Infer seconds vs milliseconds
        sample_val = float(series.dropna().iloc[0]) if len(series.dropna()) > 0 else 0
        if sample_val > 1e11:  # Milliseconds (e.g. 1672531200000)
            return pd.to_datetime(series, unit="ms", utc=True)
        elif sample_val > 1e8:  # Seconds (e.g. 1672531200)
            return pd.to_datetime(series, unit="s", utc=True)
        else:
            return pd.to_datetime(series, utc=True)
    else:
        # String representations
        parsed = pd.to_datetime(series, utc=True, errors="coerce")
        if parsed.isna().all():
            raise DataValidationError("Failed to parse any valid timestamps from the timestamp column.")
        return parsed


def validate_ohlcv_integrity(df: pd.DataFrame) -> None:
    """
    Performs institutional integrity assertions:
    1. No NaN / Inf in price columns
    2. High >= Low, High >= Open, High >= Close
    3. Low <= Open, Low <= Close
    4. Positive prices (> 0)
    5. Monotonically increasing timestamps (no lookahead / time jumps)
    """
    if df.empty:
        raise DataValidationError("Historical OHLCV DataFrame is empty.")

    # 1. Null check
    null_counts = df[["open", "high", "low", "close"]].isna().sum()
    if null_counts.any():
        raise DataValidationError(f"Null values detected in price columns:\n{null_counts}")

    # 2. Positive price check
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        raise DataValidationError("Non-positive prices found in OHLCV dataset.")

    # 3. High/Low geometry check
    invalid_high = (df["high"] < df["low"]) | (df["high"] < df["open"]) | (df["high"] < df["close"])
    if invalid_high.any():
        bad_idx = df[invalid_high].index.tolist()[:3]
        raise DataValidationError(f"High price lower than Open/Low/Close at indices: {bad_idx}")

    invalid_low = (df["low"] > df["open"]) | (df["low"] > df["close"])
    if invalid_low.any():
        bad_idx = df[invalid_low].index.tolist()[:3]
        raise DataValidationError(f"Low price higher than Open/Close at indices: {bad_idx}")

    # 4. Monotonic timestamp check
    if not df["timestamp"].is_monotonic_increasing:
        raise DataValidationError("Timestamps are not monotonically increasing. Sort required.")


def load_ohlcv_csv(
    file_path: str,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    start_time: Optional[Union[datetime, str]] = None,
    end_time: Optional[Union[datetime, str]] = None,
) -> pd.DataFrame:
    """
    Loads a local CSV file into a verified, clean OHLCV DataFrame.

    Args:
        file_path: Path to the CSV file.
        symbol: Optional symbol identifier (for logging/metadata).
        timeframe: Optional timeframe indicator (e.g. '5m', '1h').
        start_time: Optional filter start timestamp (UTC).
        end_time: Optional filter end timestamp (UTC).

    Returns:
        Structured pd.DataFrame with columns: timestamp, open, high, low, close, volume
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Historical CSV file not found: {file_path}")

    logger.info(f"Loading OHLCV CSV from {file_path} (Symbol: {symbol or 'N/A'}, TF: {timeframe or 'N/A'})...")
    raw_df = pd.read_csv(file_path)

    # Normalize columns
    df = normalize_ohlcv_columns(raw_df)

    # Parse timestamps
    df["timestamp"] = parse_timestamps(df["timestamp"])

    # Drop NaNs in timestamps and cast prices to float64
    df = df.dropna(subset=["timestamp"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Drop duplicates and sort chronologically
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    # Apply date filters if supplied
    if start_time is not None:
        start_dt = pd.to_datetime(start_time, utc=True)
        df = df[df["timestamp"] >= start_dt]
    if end_time is not None:
        end_dt = pd.to_datetime(end_time, utc=True)
        df = df[df["timestamp"] <= end_dt]

    df = df.reset_index(drop=True)

    # Validate integrity
    validate_ohlcv_integrity(df)

    logger.info(
        f"Successfully loaded {len(df)} candles from {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}."
    )
    return df


def resample_ohlcv(df: pd.DataFrame, target_timeframe: str) -> pd.DataFrame:
    """
    Resamples a higher-frequency DataFrame (e.g. 5m) to a lower-frequency one (e.g. 15m, 1h, 4h).
    """
    tf_rules = {
        "1m": "1min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1h": "1h",
        "4h": "4h",
        "1d": "1D",
    }
    rule = tf_rules.get(target_timeframe.lower(), target_timeframe)

    resampled = (
        df.set_index("timestamp")
        .resample(rule)
        .agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        })
        .dropna()
        .reset_index()
    )

    return resampled
