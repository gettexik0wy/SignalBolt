"""
Configuration menu for SignalBolt.

Features:
- View all configs
- Edit config values
- Compare configs
- Create new config
- Select active config
"""

import os
import yaml
from pathlib import Path
from typing import Optional, Dict, Any, List

from signalbolt.core.config import Config, list_configs
from signalbolt.regime.presets import print_presets
from signalbolt.cli.utils import (
    print_header,
    print_divider,
    print_table,
    input_yes_no,
    input_string,
    input_number,
    input_choice,
    green,
    red,
    yellow,
    cyan,
    bold,
    dim,
    clear_screen,
)
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.cli.config_menu")


# =============================================================================
# PATHS
# =============================================================================

ROOT_DIR = Path(__file__).parent.parent.parent
CONFIGS_DIR = ROOT_DIR / "configs"


# =============================================================================
# CONFIG MENU
# =============================================================================


class ConfigMenu:
    """
    Configuration menu.

    Usage:
        menu = ConfigMenu()
        menu.run()
    """

    def __init__(self):
        """Initialize menu."""
        self.running = True
        self.current_mode = "paper"  # paper, live, signals

    def run(self):
        """Run main menu loop."""
        while self.running:
            try:
                self._show_main_menu()
            except KeyboardInterrupt:
                print(f"\n\n{yellow('Interrupted. Use Exit to quit properly.')}")
                import time

                time.sleep(1)
            except Exception as e:
                print(f"\n{red(f'Error: {e}')}")
                import traceback

                traceback.print_exc()
                input("\nPress Enter to continue...")

    # =========================================================================
    # MAIN MENU
    # =========================================================================

    def _show_main_menu(self):
        """Show main config menu."""
        clear_screen()
        print_header("CONFIGURATION MENU")

        print(f"\n  Current mode: {cyan(self.current_mode)}\n")

        print(f"  {cyan('[1]')} 📋 View All Configs")
        print(f"  {cyan('[2]')} 📝 Edit Config")
        print(f"  {cyan('[3]')} 🔀 Compare Configs")
        print(f"  {cyan('[4]')} ➕ Create New Config")
        print(f"  {cyan('[5]')} 📄 Duplicate Config")
        print(f"  {cyan('[6]')} 🗑️  Delete Config")
        print(f"  {cyan('[7]')} 🌍 View Regime Presets")
        print(f"  {cyan('[8]')} 🔄 Switch Mode (paper/live/signals)")
        print()
        print(f"  {dim('[0]')} 🚪 Exit to Main Menu")
        print()

        choice = input(f"  {bold('Select option')}: ").strip()

        self._handle_choice(choice)

    def _handle_choice(self, choice: str):
        """Handle menu choice."""
        handlers = {
            "1": self._view_all_configs,
            "2": self._edit_config_menu,
            "3": self._compare_configs,
            "4": self._create_config,
            "5": self._duplicate_config,
            "6": self._delete_config,
            "7": self._view_presets,
            "8": self._switch_mode,
            "0": self._exit_menu,
        }

        handler = handlers.get(choice)
        if handler:
            handler()

    # =========================================================================
    # VIEW ALL CONFIGS
    # =========================================================================

    def _view_all_configs(self):
        """View all available configs."""
        clear_screen()
        print_header("AVAILABLE CONFIGURATIONS")

        configs = self._get_configs()

        if not configs:
            print(f"\n  {yellow('No configs found')}")
            input("\nPress Enter to continue...")
            return

        # Table headers
        headers = [
            "#",
            "Name",
            "Min Score",
            "Max Pos",
            "SL %",
            "Trailing %",
            "Scan Int.",
        ]
        rows = []

        for i, config_file in enumerate(configs, 1):
            try:
                config_data = self._load_config_file(config_file)

                rows.append(
                    [
                        i,
                        config_file,
                        config_data.get("scanner", {}).get("min_signal_score", "-"),
                        config_data.get("spot", {}).get("max_positions", "-"),
                        config_data.get("spot", {}).get("hard_sl_pct", "-"),
                        config_data.get("spot", {}).get("trail_distance_pct", "-"),
                        config_data.get("scanner", {}).get("scan_interval_sec", "-"),
                    ]
                )
            except Exception as e:
                rows.append([i, config_file, "Error", "-", "-", "-", "-"])

        print()
        print_table(headers, rows, ["r", "l", "r", "r", "r", "r", "r"])
        print()

        # Show details for selected config
        choice = input(f"\n  View details for config # (0 to skip): ").strip()

        if choice and choice != "0":
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(configs):
                    self._view_config_details(configs[idx])
            except ValueError:
                pass

    def _view_config_details(self, config_file: str):
        """View detailed config."""
        clear_screen()
        print_header(f"CONFIG: {config_file}")

        try:
            config_data = self._load_config_file(config_file)

            # Pretty print YAML
            print()
            print(yaml.dump(config_data, default_flow_style=False, sort_keys=False))
            print()

        except Exception as e:
            print(f"\n  {red(f'Error loading config: {e}')}\n")

        input("\nPress Enter to continue...")

    # =========================================================================
    # DELETE CONFIG
    # =========================================================================

    def _delete_config(self):
        """Delete a config file."""
        clear_screen()
        print_header("DELETE CONFIGURATION")

        configs = self._get_configs()

        if not configs:
            print(f"\n  {yellow('No configs found')}")
            input("\nPress Enter to continue...")
            return

        # Protected configs that cannot be deleted
        protected = [
            "config_safe.yaml",
            "config_balanced.yaml",
            "config_aggressive.yaml",
        ]

        print("\n  Select config to delete:\n")

        deletable_configs = []
        for i, cfg in enumerate(configs, 1):
            if cfg in protected:
                print(f"    {dim(f'{i}. {cfg} (protected)')}")
            else:
                print(f"    {cyan(str(i))}. {cfg}")
                deletable_configs.append((i, cfg))

        if not deletable_configs:
            print(f"\n  {yellow('No deletable configs (all are protected)')}")
            input("\nPress Enter to continue...")
            return

        print()
        print(f"    {dim('0. Cancel')}")
        print()

        choice = input(f"  Config # to delete: ").strip()

        if choice == "0" or not choice:
            return

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(configs):
                config_name = configs[idx]

                # Check if protected
                if config_name in protected:
                    print(f"\n  {red('Cannot delete protected config!')}")
                    input("\nPress Enter to continue...")
                    return

                # Confirm deletion
                print(f"\n  {yellow('⚠️  WARNING: This cannot be undone!')}")

                if input_yes_no(f"\n  Delete '{config_name}'?", default=False):
                    try:
                        config_path = self._get_config_path(config_name)

                        if config_path.exists():
                            config_path.unlink()  # Delete file
                            print(f"\n  {green(f'✓ Deleted {config_name}')}")
                        else:
                            print(f"\n  {red('File not found')}")

                    except Exception as e:
                        print(f"\n  {red(f'Error deleting: {e}')}")

                    import time

                    time.sleep(1)
                else:
                    print(f"\n  {dim('Cancelled')}")
                    import time

                    time.sleep(0.5)

        except ValueError:
            print(f"\n  {red('Invalid selection')}")
            input("\nPress Enter to continue...")

    # =========================================================================
    # DUPLICATE CONFIG
    # =========================================================================

    def _duplicate_config(self):
        """Duplicate existing config."""
        clear_screen()
        print_header("DUPLICATE CONFIGURATION")

        configs = self._get_configs()

        if not configs:
            print(f"\n  {yellow('No configs found')}")
            input("\nPress Enter to continue...")
            return

        print("\n  Select config to duplicate:\n")
        for i, cfg in enumerate(configs, 1):
            print(f"    {cyan(str(i))}. {cfg}")

        print()
        choice = input(f"  Config #: ").strip()

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(configs):
                source_name = configs[idx]

                # Ask for new name
                new_name = input_string(
                    "\n  New config name (without .yaml)", min_length=1
                )

                if not new_name.endswith(".yaml"):
                    new_name += ".yaml"

                # Check if exists
                new_path = self._get_config_path(new_name)
                if new_path.exists():
                    if not input_yes_no(
                        f"\n  '{new_name}' already exists. Overwrite?", default=False
                    ):
                        return

                # Copy
                try:
                    import shutil

                    source_path = self._get_config_path(source_name)

                    # Ensure directory exists
                    new_path.parent.mkdir(parents=True, exist_ok=True)

                    shutil.copy(source_path, new_path)

                    # Update name in file
                    config_data = self._load_config_file(new_name)
                    config_data["name"] = new_name.replace(".yaml", "")
                    self._save_config_file(new_name, config_data)

                    print(f"\n  {green(f'✓ Created {new_name} from {source_name}')}")

                    # Edit now?
                    if input_yes_no("\n  Edit now?", default=True):
                        self._edit_config_file(new_name)

                except Exception as e:
                    print(f"\n  {red(f'Error: {e}')}")

        except ValueError:
            print(f"\n  {red('Invalid selection')}")

        import time

        time.sleep(1)

    # =========================================================================
    # EDIT CONFIG
    # =========================================================================

    def _edit_config_menu(self):
        """Edit config menu."""
        configs = self._get_configs()

        if not configs:
            print(f"\n  {yellow('No configs found')}")
            input("\nPress Enter to continue...")
            return

        clear_screen()
        print_header("EDIT CONFIGURATION")

        print("\n  Select config to edit:\n")
        for i, cfg in enumerate(configs, 1):
            print(f"    {cyan(str(i))}. {cfg}")

        print()
        choice = input(f"  Config #: ").strip()

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(configs):
                self._edit_config_file(configs[idx])
        except ValueError:
            pass

    def _edit_config_file(self, config_file: str):
        """Edit specific config file."""
        while True:
            clear_screen()
            print_header(f"EDITING: {config_file}")

            try:
                config_data = self._load_config_file(config_file)
            except Exception as e:
                print(f"\n  {red(f'Error loading config: {e}')}")
                input("\nPress Enter to go back...")
                return

            # Show sections
            print("\n  Select section to edit:\n")

            sections = ["spot", "scanner", "discovery", "strategy", "regime_overrides"]

            for i, section in enumerate(sections, 1):
                if section in config_data:
                    print(f"    {cyan(str(i))}. {section}")

            print()
            print(f"    {dim('0. Back')}")
            print()

            choice = input(f"  Section #: ").strip()

            if choice == "0" or not choice:
                return

            try:
                idx = int(choice) - 1
                if 0 <= idx < len(sections):
                    section = sections[idx]
                    if section in config_data:
                        self._edit_section(config_data, section)
                        self._save_config_file(config_file, config_data)
            except ValueError:
                pass

    def _edit_section(self, config_data: dict, section: str):
        """Edit config section."""
        while True:
            clear_screen()
            print_header(f"EDITING: {section.upper()}")

            section_data = config_data.get(section, {})

            if not section_data:
                print(f"\n  {yellow('Section is empty')}")
                input("\nPress Enter to go back...")
                return

            print("\n  Current values:\n")

            keys = list(section_data.keys())
            for i, key in enumerate(keys, 1):
                value = section_data[key]
                print(f"    {cyan(str(i))}. {key}: {yellow(str(value))}")

            print()
            print(f"    {dim('0. Back')}")
            print()

            choice = input(f"  Select parameter to edit (0 to go back): ").strip()

            if choice == "0" or not choice:
                return

            try:
                idx = int(choice) - 1
                if 0 <= idx < len(keys):
                    key = keys[idx]
                    current = section_data[key]

                    # Edit value
                    print(f"\n  Current value: {yellow(str(current))}")
                    print(f"  Type: {type(current).__name__}")

                    if isinstance(current, bool):
                        new_value = input_yes_no(f"\n  New value", default=current)
                    elif isinstance(current, int):
                        new_value = int(
                            input_number("  New value", default=float(current))
                        )
                    elif isinstance(current, float):
                        new_value = input_number("  New value", default=current)
                    elif isinstance(current, str):
                        new_value = input_string("  New value", default=current)
                    else:
                        print(f"\n  {red('Cannot edit this type')}")
                        input("\nPress Enter to continue...")
                        continue

                    section_data[key] = new_value
                    print(f"\n  {green('✓ Updated')}")
                    import time

                    time.sleep(0.5)

            except ValueError as e:
                print(f"\n  {red(f'Invalid input: {e}')}")
                input("\nPress Enter to continue...")

    # =========================================================================
    # COMPARE CONFIGS
    # =========================================================================

    def _compare_configs(self):
        """Compare two configs side by side."""
        clear_screen()
        print_header("COMPARE CONFIGURATIONS")

        configs = self._get_configs()

        if len(configs) < 2:
            print(f"\n  {yellow('Need at least 2 configs to compare')}")
            input("\nPress Enter to continue...")
            return

        # Select first config
        print("\n  Select first config:\n")
        for i, cfg in enumerate(configs, 1):
            print(f"    {cyan(str(i))}. {cfg}")

        print()
        choice1 = int(input(f"  First config: ").strip())

        # Select second config
        print("\n  Select second config:\n")
        for i, cfg in enumerate(configs, 1):
            if i != choice1:
                print(f"    {cyan(str(i))}. {cfg}")

        print()
        choice2 = int(input(f"  Second config: ").strip())

        if (
            choice1 == choice2
            or not (1 <= choice1 <= len(configs))
            or not (1 <= choice2 <= len(configs))
        ):
            print(f"\n  {red('Invalid selection')}")
            input("\nPress Enter to continue...")
            return

        # Load configs
        try:
            config1 = self._load_config_file(configs[choice1 - 1])
            config2 = self._load_config_file(configs[choice2 - 1])
        except Exception as e:
            print(f"\n  {red(f'Error loading configs: {e}')}")
            input("\nPress Enter to continue...")
            return

        # Compare
        clear_screen()
        print_header("COMPARISON")

        print(f"\n  {bold(configs[choice1 - 1])} vs {bold(configs[choice2 - 1])}\n")

        # Compare key sections
        sections = ["spot", "scanner", "discovery"]

        for section in sections:
            print(f"\n  {bold(section.upper())}")
            print(f"  {'-' * 60}")

            if section not in config1 or section not in config2:
                print(f"    {dim('Section missing in one config')}")
                continue

            keys = set(
                list(config1.get(section, {}).keys())
                + list(config2.get(section, {}).keys())
            )

            for key in sorted(keys):
                val1 = config1.get(section, {}).get(key, "-")
                val2 = config2.get(section, {}).get(key, "-")

                if val1 == val2:
                    color = dim
                elif val1 > val2:
                    color = green
                else:
                    color = red

                print(f"    {key:<25} {color(str(val1)):>15} vs {str(val2):<15}")

        print()
        input("\nPress Enter to continue...")

    # =========================================================================
    # CREATE CONFIG
    # =========================================================================

    def _create_config(self):
        """Create new config."""
        clear_screen()
        print_header("CREATE NEW CONFIGURATION")

        name = input_string("\n  Config name (without .yaml)", min_length=1)

        if not name.endswith(".yaml"):
            name += ".yaml"

        # Check if exists
        config_path = self._get_config_path(name)
        if config_path.exists():
            if not input_yes_no(
                f"\n  Config '{name}' already exists. Overwrite?", default=False
            ):
                return

        # Select base template
        print("\n  Select base template:\n")
        print(f"    {cyan('1')}. Safe (conservative)")
        print(f"    {cyan('2')}. Balanced")
        print(f"    {cyan('3')}. Aggressive")
        print(f"    {cyan('4')}. Empty (start from scratch)")
        print()

        template_choice = input(f"  Template: ").strip()

        if template_choice == "1":
            base = "config_safe.yaml"
        elif template_choice == "2":
            base = "config_balanced.yaml"
        elif template_choice == "3":
            base = "config_aggressive.yaml"
        else:
            base = None

        # Copy or create
        if base:
            try:
                base_path = self._get_config_path(base)
                if base_path.exists():
                    import shutil

                    shutil.copy(base_path, config_path)
                    print(f"\n  {green(f'✓ Created {name} from {base}')}")
                else:
                    print(f"\n  {red(f'Template {base} not found')}")
                    return
            except Exception as e:
                print(f"\n  {red(f'Error: {e}')}")
                return
        else:
            # Create empty
            template = {
                "name": name.replace(".yaml", ""),
                "description": "Custom configuration",
                "risk_level": "MEDIUM",
                "spot": {
                    "wallet_pct": 50,
                    "max_positions": 1,
                    "hard_sl_pct": -2.0,
                },
                "scanner": {
                    "min_signal_score": 70,
                    "scan_interval_sec": 45,
                },
                "discovery": {
                    "top_coins": 30,
                },
            }

            self._save_config_file(name, template)
            print(f"\n  {green(f'✓ Created empty config {name}')}")

        # Edit now?
        if input_yes_no("\n  Edit now?", default=True):
            self._edit_config_file(name)
        else:
            import time

            time.sleep(1)

    # =========================================================================
    # VIEW PRESETS
    # =========================================================================

    def _view_presets(self):
        """View regime presets."""
        clear_screen()
        print_presets()
        input("\nPress Enter to continue...")

    # =========================================================================
    # SWITCH MODE
    # =========================================================================

    def _switch_mode(self):
        """Switch between paper/live/signals."""
        clear_screen()
        print_header("SWITCH MODE")

        print(f"\n  Current mode: {cyan(self.current_mode)}\n")
        print(f"    {cyan('1')}. Paper Trading")
        print(f"    {cyan('2')}. Live Trading")
        print(f"    {cyan('3')}. Signals Only")
        print()

        choice = input(f"  Select mode: ").strip()

        if choice == "1":
            self.current_mode = "paper"
        elif choice == "2":
            self.current_mode = "live"
        elif choice == "3":
            self.current_mode = "signals"
        else:
            return

        print(f"\n  {green(f'✓ Switched to {self.current_mode} mode')}")
        import time

        time.sleep(1)

    # =========================================================================
    # EXIT
    # =========================================================================

    def _exit_menu(self):
        """Exit menu."""
        self.running = False

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _get_configs(self) -> List[str]:
        """Get list of config files."""
        mode_dir = CONFIGS_DIR / self.current_mode

        configs = []

        # Try mode-specific directory first
        if mode_dir.exists():
            configs = [
                f.name for f in mode_dir.glob("*.yaml") if not f.name.startswith("_")
            ]

        # Fallback to root configs dir
        if not configs and CONFIGS_DIR.exists():
            configs = [
                f.name for f in CONFIGS_DIR.glob("*.yaml") if not f.name.startswith("_")
            ]

        return sorted(configs)

    def _get_config_path(self, config_file: str) -> Path:
        """Get full path to config file."""
        # Remove .yaml if already in filename to avoid double extension
        if config_file.endswith(".yaml"):
            base_name = config_file
        else:
            base_name = config_file + ".yaml"

        # Try mode-specific first
        mode_path = CONFIGS_DIR / self.current_mode / base_name
        if mode_path.exists():
            return mode_path

        # Fallback to root
        root_path = CONFIGS_DIR / base_name
        if root_path.exists():
            return root_path

        # Default to mode-specific for new files
        return mode_path

    def _load_config_file(self, config_file: str) -> dict:
        """Load config file."""
        config_path = self._get_config_path(config_file)

        with open(config_path, "r") as f:
            return yaml.safe_load(f) or {}

    def _save_config_file(self, config_file: str, data: dict):
        """Save config file."""
        config_path = self._get_config_path(config_file)

        # Ensure directory exists
        config_path.parent.mkdir(parents=True, exist_ok=True)

        with open(config_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)


# =============================================================================
# ENTRY POINT
# =============================================================================


def run_config_menu():
    """Run config menu."""
    menu = ConfigMenu()
    menu.run()


if __name__ == "__main__":
    run_config_menu()
