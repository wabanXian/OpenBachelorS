"""Audit and explicitly repair omitted ordinary mainline chapter entrances."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil

from ..util.mainline_unlock import implicit_mainline_unlock_conditions
from ..util.player_data import DeltaJson, OverlayJson, post_tutorial_player_data_template


PASS_STATE = 2


def save_dir_for(username, single_user=False):
    if single_user:
        return Path("sav") / "presets" / "post_tutorial"
    if not username:
        raise ValueError("--username is required unless --single-user is used")
    return Path("multi_sav") / "presets" / "post_tutorial" / username


def _load(save_dir):
    delta_path = save_dir / "delta.json"
    pending_path = save_dir / "pending_delta.json"
    if not delta_path.is_file():
        raise FileNotFoundError(delta_path)
    delta_obj = json.loads(delta_path.read_text(encoding="utf-8"))
    pending = (
        json.loads(pending_path.read_text(encoding="utf-8"))
        if pending_path.is_file()
        else {"modified": {}, "deleted": {}}
    )
    delta = DeltaJson(
        delta_obj.setdefault("modified", {}), delta_obj.setdefault("deleted", {})
    )
    return delta_path, delta_obj, pending, OverlayJson(
        post_tutorial_player_data_template, delta
    )


def _stage_record(stage_id):
    return {
        "stageId": stage_id,
        "completeTimes": 0,
        "startTimes": 0,
        "practiceTimes": 0,
        "state": 0,
        "hasBattleReplay": 0,
        "noCostCnt": 0,
    }


def audit_save(save_dir):
    _, _, pending, player = _load(save_dir)
    stages = player["dungeon"]["stages"]
    candidates = []
    already_present = []
    rules = implicit_mainline_unlock_conditions()
    for target_id, condition in rules.items():
        predecessor_id = condition["stageId"]
        predecessor = stages[predecessor_id].copy() if predecessor_id in stages else None
        target = stages[target_id].copy() if target_id in stages else None
        row = {
            "targetStageId": target_id,
            "predecessorStageId": predecessor_id,
            "predecessorState": predecessor.get("state") if predecessor else None,
            "predecessorCompleteTimes": predecessor.get("completeTimes") if predecessor else None,
            "targetState": target,
            "condition": "PASS",
            "source": condition["source"],
        }
        if target is not None:
            already_present.append(row)
        elif predecessor and int(predecessor.get("state", 0)) >= PASS_STATE:
            candidates.append(row)
    return {
        "saveDir": str(save_dir),
        "mainStageProgress": player["status"]["mainStageProgress"],
        "pendingDeltaEmpty": not pending.get("modified") and not pending.get("deleted"),
        "repairCandidates": candidates,
        "alreadyPresent": already_present,
        "implicitRuleCount": len(rules),
    }


def backup_save(save_dir):
    root = Path("save_backups") / "post_tutorial" / "mainline_unlock"
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(shutil.make_archive(str(root / f"{save_dir.name}-{stamp}"), "zip", save_dir))


def repair_save(save_dir, *, yes=False):
    if not yes:
        raise RuntimeError("refusing to modify a save without --yes")
    report = audit_save(save_dir)
    if not report["pendingDeltaEmpty"]:
        raise RuntimeError("refusing repair while pending_delta is not empty")
    if not report["repairCandidates"]:
        return None, {**report, "changedPaths": []}
    delta_path, delta_obj, _, player = _load(save_dir)
    changed = []
    for candidate in report["repairCandidates"]:
        target_id = candidate["targetStageId"]
        player["dungeon"]["stages"][target_id] = _stage_record(target_id)
        changed.append(f"dungeon.stages.{target_id}")
    archive = backup_save(save_dir)
    tmp_path = delta_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(delta_obj, ensure_ascii=False, indent=4), encoding="utf-8")
    tmp_path.replace(delta_path)
    return archive, {**report, "changedPaths": changed}


def main():
    parser = argparse.ArgumentParser(description="Audit mainline chapter unlocks")
    parser.add_argument("action", choices=("audit", "repair"))
    parser.add_argument("--username")
    parser.add_argument("--single-user", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    save_dir = save_dir_for(args.username, args.single_user)
    if args.action == "audit":
        print(json.dumps(audit_save(save_dir), ensure_ascii=False, indent=2))
        return
    archive, report = repair_save(save_dir, yes=args.yes)
    if archive is not None:
        print(f"backup: {archive}")
    print(json.dumps({"changedPaths": report["changedPaths"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
