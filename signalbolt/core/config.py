"""
Configuration loader with hot-reload support.

Features:
- Global config (config.yaml) + mode-specific configs
- Auto-detect config per mode (paper/live/signals)
- Hot-reload from Dashboard (safe, waits for scan end)
- Validation with warnings (not blocking)
- Session overrides support
"""

import os
import yaml
import threading
from pathlib import Path
from typing import Any, Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime


# =============================================================================
# PATHS
# =============================================================================

ROOT_DIR = Path(__file__).parent.parent.parent  # SignalBolt/
CONFIGS_DIR = ROOT_DIR / "configs"
DATA_DIR = ROOT_DIR / "data"


# =============================================================================
# CONFIG VALIDATION RULES
# =============================================================================

VALIDATION_RULES = {
    # Spot settings
    "spot.wallet_pct": {"min": 5, "max": 100, "default": 50},
    "spot.max_positions": {"min": 1, "max": 10, "default": 1},
    "spot.hard_sl_pct": {"min": -20, "max": -0.5, "default": -2.0},
    "spot.be_activation_pct": {"min": 0.1, "max": 5.0, "default": 0.5},
    "spot.trail_distance_pct": {"min": 0.1, "max": 3.0, "default": 0.4},
    "spot.timeout_minutes": {"min": 5, "max": 1440, "default": 60},
    # Scanner settings
    "scanner.min_signal_score": {"min": 30, "max": 95, "default": 70},
    "scanner.scan_interval_sec": {"min": 10, "max": 300, "default": 45},
    "scanner.signal_cooldown_min": {"min": 5, "max": 120, "default": 30},
    # Discovery settings
    "discovery.top_coins": {"min": 5, "max": 100, "default": 30},
    "discovery.min_volume_24h": {"min": 100000, "max": 1000000000, "default": 5000000},
    "discovery.max_spread_pct": {"min": 0.05, "max": 2.0, "default": 0.3},
    # Exchange settings
    "exchange.timeout_ms": {"min": 5000, "max": 60000, "default": 15000},
    "exchange.retry_count": {"min": 1, "max": 10, "default": 3},
}


# =============================================================================
# CONFIG DATA CLASS
# =============================================================================


@dataclass
class ConfigSnapshot:
    """Immutable snapshot of configuration at a point in time."""

    data: Dict[str, Any]
    loaded_at: datetime
    source_file: str
    mode: str  # 'paper', 'live', 'signals', 'global'

    def get(self, *keys, default=None) -> Any:
        """Get nested config value using dot notation or multiple keys."""
        val = self.data
        for key in keys:
            if isinstance(val, dict):
                val = val.get(key)
            else:
                return default
            if val is None:
                return default
        return val


class Config:
    """
    Configuration manager with hot-reload support.

    Usage:
        # Load global config
        config = Config()

        # Load mode-specific config
        config = Config(mode='paper', config_name='config_safe.yaml')

        # Access values
        wallet_pct = config.get('spot', 'wallet_pct', default=50)

        # Or via properties
        wallet_pct = config.WALLET_PCT

        # Hot-reload (from Dashboard)
        config.request_reload()

        # Check if reload pending
        if config.reload_pending:
            config.do_reload()
    """

    _instance: Optional["Config"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        """Singleton pattern - one config instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(
        self,
        mode: str = "global",
        config_name: Optional[str] = None,
        session_id: Optional[str] = None,
    ):
        """
        Initialize config.

        Args:
            mode: 'global', 'paper', 'live', or 'signals'
            config_name: Specific config file name (e.g., 'config_safe.yaml')
            session_id: Session ID for loading session-specific overrides
        """
        # Skip if already initialized with same params
        if self._initialized:
            return

        self._mode = mode
        self._config_name = config_name
        self._session_id = session_id

        # State
        self._global_config: Dict[str, Any] = {}
        self._mode_config: Dict[str, Any] = {}
        self._session_overrides: Dict[str, Any] = {}
        self._merged_config: Dict[str, Any] = {}

        # Hot-reload state
        self._reload_pending = False
        self._reload_lock = threading.Lock()
        self._pending_changes: Dict[str, Any] = {}

        # Metadata
        self._loaded_at: Optional[datetime] = None
        self._source_files: list = []
        self._warnings: list = []

        # Load config
        self._load()
        self._initialized = True

    # =========================================================================
    # LOADING
    # =========================================================================

    def _load(self):
        """Load all config layers."""
        self._warnings = []
        self._source_files = []

        # 1. Load global config
        self._global_config = self._load_yaml(CONFIGS_DIR / "config.yaml")

        # 2. Load mode-specific config
        if self._mode != "global":
            mode_dir = CONFIGS_DIR / self._mode

            if self._config_name:
                # Specific config requested
                mode_file = mode_dir / self._config_name
            else:
                # Auto-detect: try config_safe.yaml first
                mode_file = mode_dir / "config_safe.yaml"
                if not mode_file.exists():
                    # Fallback to any yaml in folder
                    yaml_files = list(mode_dir.glob("*.yaml"))
                    mode_file = yaml_files[0] if yaml_files else None

            if mode_file and mode_file.exists():
                self._mode_config = self._load_yaml(mode_file)

        # 3. Load session overrides (if session_id provided)
        if self._session_id:
            self._load_session_overrides()

        # 4. Merge configs (global < mode < session)
        self._merge_configs()

        # 5. Validate
        self._validate()

        # 6. Update metadata
        self._loaded_at = datetime.now()

    def _load_yaml(self, path: Path) -> Dict[str, Any]:
        """Load single YAML file."""
        if not path.exists():
            return {}

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            self._source_files.append(str(path))
            return data
        except Exception as e:
            self._warnings.append(f"Failed to load {path}: {e}")
            return {}

    def _load_session_overrides(self):
        """Load session-specific overrides."""
        session_dir = DATA_DIR / f"{self._mode}_sessions" / self._session_id
        overrides_file = session_dir / "config_overrides.yaml"

        if overrides_file.exists():
            self._session_overrides = self._load_yaml(overrides_file)

    def _merge_configs(self):
        """Merge config layers: global < mode < session."""
        # Start with global
        self._merged_config = dict(self._global_config)

        # Deep merge mode config
        self._deep_merge(self._merged_config, self._mode_config)

        # Deep merge session overrides
        self._deep_merge(self._merged_config, self._session_overrides)

    def _deep_merge(self, base: dict, override: dict):
        """Deep merge override into base."""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    # =========================================================================
    # VALIDATION
    # =========================================================================

    def _validate(self):
        """Validate config values and emit warnings."""
        for key_path, rules in VALIDATION_RULES.items():
            keys = key_path.split(".")
            value = self.get(*keys)

            if value is None:
                continue

            min_val = rules.get("min")
            max_val = rules.get("max")
            default = rules.get("default")

            if min_val is not None and value < min_val:
                self._warnings.append(
                    f"WARNING: {key_path}={value} is below minimum ({min_val}). "
                    f"Recommended: {default}"
                )

            if max_val is not None and value > max_val:
                self._warnings.append(
                    f"WARNING: {key_path}={value} is above maximum ({max_val}). "
                    f"Recommended: {default}"
                )

    # =========================================================================
    # ACCESS
    # =========================================================================

    def get(self, *keys, default=None) -> Any:
        """
        Get nested config value.

        Usage:
            config.get('spot', 'wallet_pct', default=50)
            config.get('scanner', 'min_signal_score')
        """
        val = self._merged_config
        for key in keys:
            if isinstance(val, dict):
                val = val.get(key)
            else:
                return default
            if val is None:
                return default
        return val

    def get_all(self) -> Dict[str, Any]:
        """Get entire merged config."""
        return dict(self._merged_config)

    def get_snapshot(self) -> ConfigSnapshot:
        """Get immutable snapshot of current config."""
        return ConfigSnapshot(
            data=dict(self._merged_config),
            loaded_at=self._loaded_at,
            source_file=", ".join(self._source_files),
            mode=self._mode,
        )

    # =========================================================================
    # PROPERTIES (shortcuts for common values)
    # =========================================================================

    # Exchange
    @property
    def EXCHANGE_NAME(self) -> str:
        return self.get("exchange", "name", default="binance")

    @property
    def TIMEOUT_MS(self) -> int:
        return self.get("exchange", "timeout_ms", default=15000)

    @property
    def RETRY_COUNT(self) -> int:
        return self.get("exchange", "retry_count", default=3)

    @property
    def TAKER_FEE_PCT(self) -> float:
        return self.get("exchange", "taker_fee_pct", default=0.04)

    @property
    def MAKER_FEE_PCT(self) -> float:
        return self.get("exchange", "maker_fee_pct", default=0.0)

    # Spot
    @property
    def WALLET_PCT(self) -> float:
        return self.get("spot", "wallet_pct", default=50)

    @property
    def MAX_POSITIONS(self) -> int:
        return self.get("spot", "max_positions", default=1)

    @property
    def HARD_SL_PCT(self) -> float:
        return self.get("spot", "hard_sl_pct", default=-2.0)

    @property
    def BE_ACTIVATION_PCT(self) -> float:
        return self.get("spot", "be_activation_pct", default=0.5)

    @property
    def TRAIL_DISTANCE_PCT(self) -> float:
        return self.get("spot", "trail_distance_pct", default=0.4)

    @property
    def TIMEOUT_MINUTES(self) -> int:
        return self.get("spot", "timeout_minutes", default=60)

    # Scanner
    @property
    def MIN_SIGNAL_SCORE(self) -> float:
        return self.get("scanner", "min_signal_score", default=70)

    @property
    def SCAN_INTERVAL_SEC(self) -> int:
        return self.get("scanner", "scan_interval_sec", default=45)

    @property
    def SIGNAL_COOLDOWN_MIN(self) -> int:
        return self.get("scanner", "signal_cooldown_min", default=30)

    # Discovery
    @property
    def TOP_COINS(self) -> int:
        return self.get("discovery", "top_coins", default=30)

    @property
    def MIN_VOLUME_24H(self) -> float:
        return self.get("discovery", "min_volume_24h", default=5000000)

    @property
    def MAX_SPREAD_PCT(self) -> float:
        return self.get("discovery", "max_spread_pct", default=0.3)

    # Logging
    @property
    def LOG_LEVEL(self) -> str:
        return self.get("logs", "level", default="minimal")

    # Alerts
    @property
    def TELEGRAM_ENABLED(self) -> bool:
        return self.get("alerts", "telegram_enabled", default=False)

    @property
    def DISCORD_ENABLED(self) -> bool:
        return self.get("alerts", "discord_enabled", default=False)

    # Dashboard
    @property
    def DASHBOARD_PORT(self) -> int:
        return self.get("dashboard", "port", default=5000)

    # =========================================================================
    # HOT-RELOAD
    # =========================================================================

    @property
    def reload_pending(self) -> bool:
        """Check if reload is requested."""
        with self._reload_lock:
            return self._reload_pending

    def request_reload(self, changes: Optional[Dict[str, Any]] = None):
        """
        Request config reload (called from Dashboard).

        Args:
            changes: Optional dict of specific changes to apply
        """
        with self._reload_lock:
            self._reload_pending = True
            if changes:
                self._deep_merge(self._pending_changes, changes)

    def do_reload(self) -> bool:
        """
        Perform reload (called from main loop when safe).

        Returns:
            True if reload was performed
        """
        with self._reload_lock:
            if not self._reload_pending:
                return False

            # Apply pending changes to session overrides
            if self._pending_changes:
                self._deep_merge(self._session_overrides, self._pending_changes)
                self._save_session_overrides()
                self._pending_changes = {}

            # Reload from files
            self._load()

            self._reload_pending = False
            return True

    def _save_session_overrides(self):
        """Save session overrides to file."""
        if not self._session_id:
            return

        session_dir = DATA_DIR / f"{self._mode}_sessions" / self._session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        overrides_file = session_dir / "config_overrides.yaml"

        with open(overrides_file, "w", encoding="utf-8") as f:
            yaml.dump(self._session_overrides, f, default_flow_style=False)

    def update_value(self, key_path: str, value: Any):
        """
        Update single value (from Dashboard).

        Args:
            key_path: Dot-separated path, e.g., 'spot.wallet_pct'
            value: New value
        """
        keys = key_path.split(".")
        changes = {}

        # Build nested dict
        current = changes
        for key in keys[:-1]:
            current[key] = {}
            current = current[key]
        current[keys[-1]] = value

        self.request_reload(changes)

    # =========================================================================
    # INFO
    # =========================================================================

    @property
    def warnings(self) -> list:
        """Get validation warnings."""
        return list(self._warnings)

    @property
    def source_files(self) -> list:
        """Get list of loaded config files."""
        return list(self._source_files)

    @property
    def mode(self) -> str:
        """Get current mode."""
        return self._mode

    def __repr__(self) -> str:
        return (
            f"Config(mode={self._mode}, "
            f"sources={len(self._source_files)}, "
            f"warnings={len(self._warnings)})"
        )

    # =========================================================================
    # CLASS METHODS
    # =========================================================================

    @classmethod
    def reset(cls):
        """Reset singleton (for testing)."""
        cls._instance = None

    @classmethod
    def load_for_mode(cls, mode: str, config_name: Optional[str] = None) -> "Config":
        """
        Load config for specific mode.

        Args:
            mode: 'paper', 'live', or 'signals'
            config_name: Optional specific config file

        Returns:
            Config instance
        """
        cls.reset()
        return cls(mode=mode, config_name=config_name)

    @classmethod
    def list_available_configs(cls, mode: str = "paper") -> list:
        """
        List available configs for mode.

        Args:
            mode: 'paper', 'live', or 'signals'

        Returns:
            List of config file names
        """
        mode_dir = CONFIGS_DIR / mode

        if not mode_dir.exists():
            # Fallback to main configs dir
            configs = []
            if CONFIGS_DIR.exists():
                for f in CONFIGS_DIR.glob("*.yaml"):
                    if not f.name.startswith("_"):
                        configs.append(f.name)
            return sorted(configs)

        return sorted(
            [f.name for f in mode_dir.glob("*.yaml") if not f.name.startswith("_")]
        )


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================


def get_config() -> Config:
    """Get current config instance."""
    return Config()


def list_configs(mode: str = "paper") -> list:
    """
    List available config files for a mode.

    Args:
        mode: 'paper', 'live', or 'signals'

    Returns:
        List of config file names

    Usage:
        from signalbolt.core.config import list_configs
        configs = list_configs('paper')
    """
    return Config.list_available_configs(mode)
