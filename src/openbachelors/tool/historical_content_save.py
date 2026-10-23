"""Set, show, or reset the server-local historical content date."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil

from ..const.filepath import HISTORICAL_CONTENT_CONFIG
from ..util.historical_release import content_date_status


def _date_value(value):
    parsed = datetime.strptime(value, "%Y-%m-%d").date()
    return {"schema": 1, "date": parsed.isoformat(), "mode": "historical"}


def show_date(config_path=Path(HISTORICAL_CONTENT_CONFIG), *, legacy_root=Path(".")):
    status = content_date_status(config_path, legacy_root)
    return {
        "configPath": status["configPath"],
        "mode": status["mode"],
        "date": status["date"].isoformat() if status["date"] else None,
        "source": status["source"],
        "legacySelections": status["legacy"],
        "legacyConflicts": status["conflicts"],
        "affects": "content_availability_only",
    }


def _backup(path):
    if not path.is_file():
        return None
    root = Path("save_backups") / "historical_content"
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination = root / f"historical-content-{stamp}.json"
    shutil.copy2(path, destination)
    return destination


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=4), encoding="utf-8")
    tmp.replace(path)


def set_date(config_path, value, *, yes=False, legacy_root=Path(".")):
    if not yes:
        raise RuntimeError("refusing to modify local configuration without --yes")
    path = Path(config_path)
    desired = _date_value(value)
    current = None
    if path.is_file():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = None
    if current == desired:
        return None, show_date(path, legacy_root=legacy_root)
    backup = _backup(path)
    _write(path, desired)
    return backup, show_date(path, legacy_root=legacy_root)


def reset_date(config_path, *, yes=False, legacy_root=Path(".")):
    if not yes:
        raise RuntimeError("refusing to modify local configuration without --yes")
    path = Path(config_path)
    desired = {"schema": 1, "date": None, "mode": "realtime"}
    current = None
    if path.is_file():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = None
    if current == desired:
        return None, show_date(path, legacy_root=legacy_root)
    backup = _backup(path)
    _write(path, desired)
    return backup, show_date(path, legacy_root=legacy_root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("set-date", "show-date", "reset-date"))
    parser.add_argument("--date")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    config_path = Path(HISTORICAL_CONTENT_CONFIG)
    if args.action == "show-date":
        report = show_date(config_path)
        backup = None
    elif args.action == "set-date":
        if not args.date:
            parser.error("set-date requires --date YYYY-MM-DD")
        backup, report = set_date(config_path, args.date, yes=args.yes)
    else:
        backup, report = reset_date(config_path, yes=args.yes)
    print(json.dumps({"backup": str(backup) if backup else None, **report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
