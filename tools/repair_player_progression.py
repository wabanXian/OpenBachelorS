"""Safely preview or repair Doctor progression in one filesystem save.

Example (preview is the default):
  uv run python tools/repair_player_progression.py --save-dir multi_sav/presets/post_tutorial/test123

Apply only after reviewing the preview:
  uv run python tools/repair_player_progression.py --save-dir multi_sav/presets/post_tutorial/test123 --apply
"""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from openbachelors.util.player_progression import calculate_player_progression
from openbachelors.const.filepath import GAMEDATA_CONST
from openbachelors.util.const_json_loader import const_json_loader


INITIAL_AP = int(const_json_loader[GAMEDATA_CONST]["playerApMap"][0])
BASE_STATUS = {"level": 1, "exp": 0, "ap": INITIAL_AP, "maxAp": INITIAL_AP}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--save-dir", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    save_dir = args.save_dir.resolve()
    delta_path = save_dir / "delta.json"
    extra_path = save_dir / "extra.json"
    if not delta_path.is_file() or not extra_path.is_file():
        raise SystemExit("save directory must contain delta.json and extra.json")
    extra = json.loads(extra_path.read_text(encoding="utf-8"))
    if extra.get("player_preset") != "post_tutorial":
        raise SystemExit("refusing non-post_tutorial save")

    delta = json.loads(delta_path.read_text(encoding="utf-8"))
    modified_status = delta.setdefault("modified", {}).setdefault("status", {})
    before = {
        key: int(modified_status.get(key, default))
        for key, default in BASE_STATUS.items()
    }
    result = calculate_player_progression(
        before["level"], before["exp"], before["ap"], 0
    )
    after = {key: result[key] for key in BASE_STATUS}
    print(json.dumps({"saveDir": str(save_dir), "before": before, "after": after,
                      "levelUps": result["levelUps"]}, indent=2))
    if not args.apply or before == after:
        print("dry-run" if not args.apply else "already consistent")
        return

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = save_dir / "backup" / f"player-progression-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    for name in ("delta.json", "pending_delta.json", "extra.json"):
        source = save_dir / name
        if source.exists():
            shutil.copy2(source, backup_dir / name)
    deleted_status = delta.setdefault("deleted", {}).setdefault("status", {})
    for key, value in after.items():
        modified_status[key] = value
        deleted_status[key] = None

    fd, temporary = tempfile.mkstemp(prefix="delta.", suffix=".tmp", dir=save_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(delta, stream, ensure_ascii=False, indent=4)
            stream.write("\n")
        os.replace(temporary, delta_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"applied; backup={backup_dir}")


if __name__ == "__main__":
    main()
