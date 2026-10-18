import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import zipfile

from ..util.building_state import (
    BUILDING_SCHEMA_VERSION,
    ensure_tutorial_first_trade_order,
)


STRUCTURAL_KEYS = {"roomSlots", "rooms", "status", "assist", "solution"}


def save_dir_for(username, single_user=False):
    if single_user:
        return Path("sav") / "presets" / "post_tutorial"
    if not username:
        raise ValueError("--username is required unless --single-user is used")
    return Path("multi_sav") / "presets" / "post_tutorial" / username


def inspect_save(save_dir):
    delta_path = save_dir / "delta.json"
    extra_path = save_dir / "extra.json"
    if not delta_path.is_file() or not extra_path.is_file():
        raise FileNotFoundError(f"post_tutorial save is incomplete: {save_dir}")
    delta = json.loads(delta_path.read_text(encoding="utf-8"))
    extra = json.loads(extra_path.read_text(encoding="utf-8"))
    building_modified = delta.get("modified", {}).get("building", {})
    building_deleted = delta.get("deleted", {}).get("building", {})
    structural_modified = sorted(STRUCTURAL_KEYS.intersection(building_modified))
    structural_deleted = sorted(STRUCTURAL_KEYS.intersection(building_deleted))
    assigned_chars = sorted(
        str(char.get("charInstId", inst_id))
        for inst_id, char in building_modified.get("chars", {}).items()
        if isinstance(char, dict) and char.get("roomSlotId")
    )
    return {
        "saveDir": str(save_dir),
        "schemaVersion": extra.get("building_schema_version"),
        "progress": delta.get("modified", {}).get("status", {}).get("progress"),
        "modifiedBuildingKeys": sorted(building_modified),
        "structuralModified": structural_modified,
        "structuralDeleted": structural_deleted,
        "assignedBuildingChars": assigned_chars,
        "safeAutomaticCandidate": not structural_modified
        and not structural_deleted
        and not assigned_chars,
    }


def backup_save(save_dir):
    root = Path("save_backups") / "post_tutorial" / "building"
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(shutil.make_archive(str(root / f"{save_dir.name}-{stamp}"), "zip", save_dir))


def migrate_save(save_dir, *, yes=False):
    report = inspect_save(save_dir)
    if not report["safeAutomaticCandidate"]:
        raise RuntimeError(
            "refusing migration: this account contains real building structural changes; "
            "inspect and migrate it manually"
        )
    if not yes:
        raise RuntimeError("refusing to modify a save without --yes")
    archive = backup_save(save_dir)
    extra_path = save_dir / "extra.json"
    extra = json.loads(extra_path.read_text(encoding="utf-8"))
    extra["building_schema_version"] = BUILDING_SCHEMA_VERSION
    tmp_path = extra_path.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(extra, ensure_ascii=False, indent=4), encoding="utf-8"
    )
    tmp_path.replace(extra_path)
    return archive


def restore_schema_marker(save_dir, archive, *, yes=False):
    """Restore only extra.json from a migration backup, preserving later gameplay."""
    if not yes:
        raise RuntimeError("refusing to modify a save without --yes")
    archive = Path(archive)
    if not archive.is_file():
        raise FileNotFoundError(archive)
    backup_save(save_dir)
    with zipfile.ZipFile(archive) as source:
        names = {name.replace("\\", "/") for name in source.namelist()}
        if "extra.json" not in names:
            raise RuntimeError("backup archive does not contain extra.json")
        restored = json.loads(source.read("extra.json").decode("utf-8"))
    extra_path = save_dir / "extra.json"
    tmp_path = extra_path.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(restored, ensure_ascii=False, indent=4), encoding="utf-8"
    )
    tmp_path.replace(extra_path)


def repair_interrupted_slot33_tutorial(save_dir, *, yes=False):
    """Advance only the Part0 story after its slot_33 clean already committed."""
    if not yes:
        raise RuntimeError("refusing to modify a save without --yes")
    delta_path = save_dir / "delta.json"
    pending_path = save_dir / "pending_delta.json"
    delta = json.loads(delta_path.read_text(encoding="utf-8"))
    pending = (
        json.loads(pending_path.read_text(encoding="utf-8"))
        if pending_path.is_file()
        else {"modified": {}, "deleted": {}}
    )
    if pending.get("modified") or pending.get("deleted"):
        raise RuntimeError("refusing repair while pending_delta is not empty")
    modified = delta.setdefault("modified", {})
    status = modified.setdefault("status", {})
    flags = status.setdefault("flags", {})
    slot = (
        modified.get("building", {})
        .get("roomSlots", {})
        .get("slot_33")
    )
    expected_slot = {
        "level": 1,
        "state": 2,
        "roomId": "ELEVATOR",
        "charInstIds": [],
        "completeConstructTime": -1,
    }
    story_id = "obt/guide/l0-11/0_building_init"
    if int(status.get("progress", -1)) != 1100:
        raise RuntimeError("repair precondition failed: progress is not 1100")
    if flags.get(story_id):
        raise RuntimeError("repair precondition failed: Part0 is already complete")
    if slot != expected_slot:
        raise RuntimeError("repair precondition failed: slot_33 is not the confirmed cleaned state")
    archive = backup_save(save_dir)
    status["progress"] = 1101
    flags[story_id] = 1
    tmp_path = delta_path.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(delta, ensure_ascii=False, indent=4), encoding="utf-8"
    )
    tmp_path.replace(delta_path)
    return archive


def seed_tutorial_trade_order(save_dir, *, yes=False):
    """Back up and seed only the missing Part11 tutorial order."""
    if not yes:
        raise RuntimeError("refusing to modify a save without --yes")
    delta_path = save_dir / "delta.json"
    pending_path = save_dir / "pending_delta.json"
    delta = json.loads(delta_path.read_text(encoding="utf-8"))
    pending = (
        json.loads(pending_path.read_text(encoding="utf-8"))
        if pending_path.is_file()
        else {"modified": {}, "deleted": {}}
    )
    if pending.get("modified") or pending.get("deleted"):
        raise RuntimeError("refusing repair while pending_delta is not empty")
    modified = delta.setdefault("modified", {})
    result = ensure_tutorial_first_trade_order(modified)
    if result["status"] != "seeded":
        raise RuntimeError(
            "repair precondition failed: tutorial order is not the sole missing state: "
            + ",".join(result["reasons"])
        )
    archive = backup_save(save_dir)
    tmp_path = delta_path.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(delta, ensure_ascii=False, indent=4), encoding="utf-8"
    )
    tmp_path.replace(delta_path)
    return archive, result


def main():
    parser = argparse.ArgumentParser(
        description="Inspect or explicitly migrate an isolated post_tutorial RIIC save"
    )
    parser.add_argument(
        "action",
        choices=(
            "inspect",
            "migrate",
            "restore",
            "repair-slot33-tutorial",
            "seed-tutorial-trade-order",
        ),
    )
    parser.add_argument("--username")
    parser.add_argument("--single-user", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--archive")
    args = parser.parse_args()
    save_dir = save_dir_for(args.username, args.single_user)
    if args.action == "inspect":
        print(json.dumps(inspect_save(save_dir), ensure_ascii=False, indent=2))
        return
    if args.action == "restore":
        if not args.archive:
            raise RuntimeError("restore requires --archive")
        restore_schema_marker(save_dir, args.archive, yes=args.yes)
        print(f"restored building schema marker: {save_dir}")
        return
    if args.action == "repair-slot33-tutorial":
        archive = repair_interrupted_slot33_tutorial(save_dir, yes=args.yes)
        print(f"backup: {archive}")
        print(f"repaired interrupted slot_33 tutorial: {save_dir}")
        return
    if args.action == "seed-tutorial-trade-order":
        archive, result = seed_tutorial_trade_order(save_dir, yes=args.yes)
        print(f"backup: {archive}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    archive = migrate_save(save_dir, yes=args.yes)
    print(f"backup: {archive}")
    print(f"migrated building schema: {save_dir}")


if __name__ == "__main__":
    main()
