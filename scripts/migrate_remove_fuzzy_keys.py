"""One-time migration: remove deprecated fuzzy-related keys from config.json.

Usage:
  python scripts/migrate_remove_fuzzy_keys.py [--apply]

Without `--apply` the script will show what it would change. With `--apply` it will
create a timestamped backup of the existing `config.json` and write the cleaned file.
"""
import json
import shutil
import sys
from datetime import datetime

from filesearch.constants import LOG_DIR

FILENAME = LOG_DIR / "config.json"
REMOVED_KEYS = ["fuzzy_sensitivity", "fuzzy_threshold", "weight_filename", "weight_dir"]


def load_config(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Failed to load config: {e}")
        return None


def write_config(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main(apply_changes=False):
    print(f"Config path: {FILENAME}")
    if not FILENAME.exists():
        print("No config file present; nothing to do.")
        return 0

    cfg = load_config(FILENAME)
    if cfg is None:
        return 2

    present = [k for k in REMOVED_KEYS if k in cfg]
    if not present:
        print("No deprecated fuzzy keys found; nothing to do.")
        return 0

    print("Deprecated keys found:")
    for k in present:
        print(" -", k, ":", cfg.get(k))

    if not apply_changes:
        print("Run with --apply to remove these keys and backup the config.")
        return 0

    # backup
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = FILENAME.with_name(f"config.json.bak.{ts}")
    shutil.copy2(FILENAME, backup)
    print(f"Backup created: {backup}")

    for k in present:
        cfg.pop(k, None)

    write_config(FILENAME, cfg)
    print("Deprecated keys removed and config saved.")
    return 0


if __name__ == '__main__':
    apply_flag = '--apply' in sys.argv[1:]
    sys.exit(main(apply_changes=apply_flag))
