"""Set, show, or reset an account-scoped historical content date."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil

from ..util.historical_release import CONTENT_DATE_KEY


def save_dir_for(username):
    if not username:
        raise ValueError("--username is required")
    return Path("multi_sav") / "presets" / "post_tutorial" / username


def _load(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _date_value(value):
    # Strict calendar validation; storage remains human-auditable.
    parsed = datetime.strptime(value, "%Y-%m-%d").date()
    return {"date": parsed.isoformat(), "mode": "historical"}


def show_date(save_dir):
    path = save_dir / "extra.json"
    value = _load(path).get(CONTENT_DATE_KEY)
    return {
        "saveDir": str(save_dir),
        "mode": "historical" if isinstance(value, dict) and value.get("date") else "realtime",
        "date": value.get("date") if isinstance(value, dict) else None,
        "affects": "content_availability_only",
    }


def _backup(save_dir):
    root = Path("save_backups") / "post_tutorial" / "historical_content"
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return Path(shutil.make_archive(str(root / f"{save_dir.name}-{stamp}"), "zip", save_dir))


def _write(path, value):
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=4), encoding="utf-8")
    tmp.replace(path)


def set_date(save_dir, value, *, yes=False):
    if not yes:
        raise RuntimeError("refusing to modify a save without --yes")
    path = save_dir / "extra.json"
    current = _load(path)
    desired = _date_value(value)
    if current.get(CONTENT_DATE_KEY) == desired:
        return None, show_date(save_dir)
    backup = _backup(save_dir)
    current[CONTENT_DATE_KEY] = desired
    # Remove the obsolete additive selector so it cannot silently conflict.
    current.pop("historical_content_v2", None)
    _write(path, current)
    return backup, show_date(save_dir)


def reset_date(save_dir, *, yes=False):
    if not yes:
        raise RuntimeError("refusing to modify a save without --yes")
    path = save_dir / "extra.json"
    current = _load(path)
    if CONTENT_DATE_KEY not in current and "historical_content_v2" not in current:
        return None, show_date(save_dir)
    backup = _backup(save_dir)
    current.pop(CONTENT_DATE_KEY, None)
    current.pop("historical_content_v2", None)
    _write(path, current)
    return backup, show_date(save_dir)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("set-date", "show-date", "reset-date"))
    parser.add_argument("--username", required=True)
    parser.add_argument("--date")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    save_dir = save_dir_for(args.username)
    if args.action == "show-date":
        report = show_date(save_dir)
        backup = None
    elif args.action == "set-date":
        if not args.date:
            parser.error("set-date requires --date YYYY-MM-DD")
        backup, report = set_date(save_dir, args.date, yes=args.yes)
    else:
        backup, report = reset_date(save_dir, yes=args.yes)
    print(json.dumps({"backup": str(backup) if backup else None, **report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
