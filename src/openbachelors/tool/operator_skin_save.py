"""Audit and explicitly repair legacy sandbox-derived operator outfits."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil

from ..const.filepath import SKIN_TABLE
from ..util.const_json_loader import const_json_loader
from ..util.operator_skin import equip_resource_default_skin, equipped_skin_id
from ..util.player_data import (
    DeltaJson,
    OverlayJson,
    post_tutorial_player_data_template,
    sandbox_player_data_template,
)


def save_dir_for(username, single_user=False):
    if single_user:
        return Path("sav") / "presets" / "post_tutorial"
    if not username:
        raise ValueError("--username is required unless --single-user is used")
    return Path("multi_sav") / "presets" / "post_tutorial" / username


def _load_overlay(save_dir):
    delta_path = save_dir / "delta.json"
    pending_path = save_dir / "pending_delta.json"
    if not delta_path.is_file():
        raise FileNotFoundError(delta_path)
    delta_obj = json.loads(delta_path.read_text(encoding="utf-8"))
    pending_obj = (
        json.loads(pending_path.read_text(encoding="utf-8"))
        if pending_path.is_file()
        else {"modified": {}, "deleted": {}}
    )
    delta = DeltaJson(
        delta_obj.setdefault("modified", {}), delta_obj.setdefault("deleted", {})
    )
    return delta_path, delta_obj, pending_obj, OverlayJson(
        post_tutorial_player_data_template, delta
    )


def _sandbox_char_by_id(char_id):
    return next(
        char.copy()
        for _, char in sandbox_player_data_template["troop"]["chars"]
        if char["charId"] == char_id
    )


def audit_save(save_dir):
    _, delta_obj, pending, player = _load_overlay(save_dir)
    skin_ts = player["skin"]["skinTs"].copy()
    records = []
    candidates = []
    for inst_id, char_value in player["troop"]["chars"]:
        char = char_value.copy()
        expected = equip_resource_default_skin(char, char.get("evolvePhase", 0))
        legacy = _sandbox_char_by_id(char["charId"])
        differences = []
        if char.get("skin") != expected.get("skin"):
            legacy_match = char.get("skin") == legacy.get("skin")
            differences.append(
                {
                    "path": f"troop.chars.{inst_id}.skin",
                    "actual": char.get("skin"),
                    "expected": expected.get("skin"),
                    "legacySandboxValue": legacy.get("skin"),
                    "legacySandboxMatch": legacy_match,
                    "skinTs": skin_ts.get(char.get("skin")),
                    "repairCandidate": legacy_match,
                    "manualSelectionCannotBeExcluded": True,
                }
            )
        for tmpl_id, tmpl_value in char.get("tmpl", {}).items():
            actual = tmpl_value.get("skinId")
            wanted = expected["tmpl"][tmpl_id].get("skinId")
            if actual == wanted:
                continue
            legacy_value = (legacy.get("tmpl", {}).get(tmpl_id) or {}).get("skinId")
            legacy_match = actual == legacy_value
            differences.append(
                {
                    "path": f"troop.chars.{inst_id}.tmpl.{tmpl_id}.skinId",
                    "actual": actual,
                    "expected": wanted,
                    "legacySandboxValue": legacy_value,
                    "legacySandboxMatch": legacy_match,
                    "skinTs": skin_ts.get(actual),
                    "repairCandidate": legacy_match,
                    "manualSelectionCannotBeExcluded": False,
                }
            )
        record = {
            "charInstId": int(inst_id),
            "charId": char["charId"],
            "evolvePhase": char.get("evolvePhase"),
            "gainTime": char.get("gainTime"),
            "differences": differences,
        }
        records.append(record)
        candidates.extend(
            {"charInstId": int(inst_id), "charId": char["charId"], **difference}
            for difference in differences
            if difference["repairCandidate"]
        )
    owned = player["skin"]["characterSkins"].copy()
    equipped_by_skin = {}
    for record in records:
        inst_id = str(record["charInstId"])
        char = player["troop"]["chars"][inst_id].copy()
        current_tmpl = char.get("currentTmpl")
        skin_id = (
            (char.get("tmpl", {}).get(current_tmpl) or {}).get("skinId")
            if current_tmpl
            else char.get("skin")
        )
        if skin_id:
            equipped_by_skin.setdefault(skin_id, []).append(record["charInstId"])
    candidate_actual = {
        candidate["actual"] for candidate in candidates if candidate["actual"]
    }
    skin_table = const_json_loader[SKIN_TABLE].copy()
    owned_char_ids = {
        char["charId"] for _, char in player["troop"]["chars"]
    }
    legacy_skin_ids = {}
    for char_id in owned_char_ids:
        legacy = _sandbox_char_by_id(char_id)
        values = {legacy.get("skin")}
        values.update(
            loadout.get("skinId") for loadout in legacy.get("tmpl", {}).values()
        )
        legacy_skin_ids[char_id] = {value for value in values if value}
    owned_records = [
        _owned_skin_record(
            skin_table,
            skin_id,
            skin_ts.get(skin_id),
            equipped_by_skin.get(skin_id, []),
            legacy_skin_ids,
            skin_id in candidate_actual,
        )
        for skin_id in sorted(owned)
    ]
    owned_char_inst = {
        char["charId"]: int(inst_id) for inst_id, char in player["troop"]["chars"]
    }
    removal_candidates = []
    for record in owned_records:
        if not (
            record["isBuySkin"]
            and record["legacySandboxValueMatch"]
            and record["charId"] in owned_char_inst
            and not record["isBuiltInSkin"]
        ):
            continue
        inst_id = owned_char_inst[record["charId"]]
        char = player["troop"]["chars"][str(inst_id)].copy()
        default = equip_resource_default_skin(char, char.get("evolvePhase", 0))
        default_skin = (
            default["tmpl"][default["currentTmpl"]]["skinId"]
            if default.get("currentTmpl")
            else default.get("skin")
        )
        removal_candidates.append(
            {
                **record,
                "classification": "confirmed_legacy_sandbox_grant",
                "charInstId": inst_id,
                "replacementDefaultSkin": default_skin,
                "replacementSkinTs": record["skinTs"],
                "skinSpPresent": record["skinId"] in player["skin"]["skinSp"],
                "evidence": [
                    "extra outfit equals the old sandbox source operator's equipped skin",
                    "old normal-account grant code copied that sandbox operator and granted its equipped skin",
                    "operator is present in the account and the outfit is not a built-in evolve/patch skin",
                    "no independent purchase/mail/activity acquisition record exists in this save",
                ],
                "timestampAssessment": (
                    "dynamic legacy gacha grant timestamp"
                    if record["skinTs"] != 1700000000
                    else "fixed legacy character-grant timestamp"
                ),
            }
        )
    uncertain_owned = [
        record
        for record in owned_records
        if record["isBuySkin"]
        and record["skinId"] not in {
            candidate["skinId"] for candidate in removal_candidates
        }
    ]
    retained_defaults = []
    for _, char_value in player["troop"]["chars"]:
        char = char_value.copy()
        expected = equip_resource_default_skin(char, char.get("evolvePhase", 0))
        skin_id = equipped_skin_id(expected)
        if skin_id:
            retained_defaults.append(skin_id)
    retained_defaults = sorted(set(retained_defaults))
    return {
        "saveDir": str(save_dir),
        "operatorCount": len(records),
        "ownedSkinCount": len(owned),
        "ownedSkins": owned_records,
        "pendingDeltaEmpty": not pending.get("modified") and not pending.get("deleted"),
        "operators": records,
        "repairCandidates": candidates,
        "repairCandidateOperatorCount": len(
            {candidate["charInstId"] for candidate in candidates}
        ),
        "ownershipRemovalCandidates": removal_candidates,
        "ownershipRemovalCandidateCount": len(removal_candidates),
        "uncertainExtraOutfits": uncertain_owned,
        "retainedBuiltInSkins": retained_defaults,
        "acquisitionEvidenceFields": {
            "mailModified": "mail" in delta_obj.get("modified", {}),
            "shopModified": "shop" in delta_obj.get("modified", {}),
            "activityModified": "activity" in delta_obj.get("modified", {}),
            "skinSp": player["skin"]["skinSp"].copy(),
            "deltaTopLevel": sorted(delta_obj.get("modified", {})),
        },
        "ownershipPolicy": (
            "audit is read-only; repair changes equipment only; "
            "revoke-legacy-outfits removes only previewed confirmed candidates"
        ),
    }


def _owned_skin_record(
    skin_table, skin_id, timestamp, equipped_by, legacy_skin_ids, equipped_candidate
):
    resource = skin_table["charSkins"].copy().get(skin_id, {})
    char_id = resource.get("charId")
    legacy_match = skin_id in legacy_skin_ids.get(char_id, set())
    evolve_values = set(
        (skin_table["buildinEvolveMap"].copy().get(char_id) or {}).values()
    )
    patch_values = set(
        (skin_table["buildinPatchMap"].copy().get(char_id) or {}).values()
    )
    return {
        "skinId": skin_id,
        "charId": char_id,
        "skinTs": timestamp,
        "equippedBy": equipped_by,
        "isBuySkin": resource.get("isBuySkin"),
        "isBuiltInSkin": skin_id in evolve_values or skin_id in patch_values,
        "legacySandboxValueMatch": legacy_match,
        "legacySandboxGrantCandidate": equipped_candidate,
        "manualAcquisitionCannotBeExcluded": bool(resource.get("isBuySkin")),
        "automaticOwnershipRemoval": False,
    }


def _set_path(root, path, value):
    node = root
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


def backup_save(save_dir):
    root = Path("save_backups") / "post_tutorial" / "operator_skin"
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
        raise RuntimeError("no legacy sandbox-derived skin candidates found")
    delta_path, delta_obj, _, player = _load_overlay(save_dir)
    before = player.copy()
    for candidate in report["repairCandidates"]:
        relative = candidate["path"].split(".")
        _set_path(player, relative, candidate["expected"])
    after = player.copy()
    allowed = {candidate["path"] for candidate in report["repairCandidates"]}
    # The archive is made before the atomic delta replacement. Ownership is
    # intentionally untouched even when the old outfit was granted by the bug.
    archive = backup_save(save_dir)
    tmp_path = delta_path.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(delta_obj, ensure_ascii=False, indent=4), encoding="utf-8"
    )
    tmp_path.replace(delta_path)
    return archive, {**report, "changedPaths": sorted(allowed), "before": before, "after": after}


def revoke_legacy_outfits(save_dir, *, yes=False):
    """Repair equipment and revoke only previewed sandbox-derived outfits."""
    if not yes:
        raise RuntimeError("refusing to modify a save without --yes")
    report = audit_save(save_dir)
    if not report["pendingDeltaEmpty"]:
        raise RuntimeError("refusing repair while pending_delta is not empty")
    candidates = report["ownershipRemovalCandidates"]
    if not candidates:
        raise RuntimeError("no confirmed legacy outfit ownership candidates found")
    delta_path, delta_obj, _, player = _load_overlay(save_dir)
    changed_paths = set()

    # Preserve the equipment-only repair behavior as part of the comprehensive
    # action, using exactly the already-previewed legacy matches.
    for candidate in report["repairCandidates"]:
        relative = candidate["path"].split(".")
        _set_path(player, relative, candidate["expected"])
        changed_paths.add(candidate["path"])

    for candidate in candidates:
        skin_id = candidate["skinId"]
        default_skin = candidate["replacementDefaultSkin"]
        timestamp = candidate["replacementSkinTs"]
        if skin_id in player["skin"]["characterSkins"]:
            del player["skin"]["characterSkins"][skin_id]
            changed_paths.add(f"skin.characterSkins.{skin_id}")
        if skin_id in player["skin"]["skinTs"]:
            del player["skin"]["skinTs"][skin_id]
            changed_paths.add(f"skin.skinTs.{skin_id}")
        if skin_id in player["skin"]["skinSp"]:
            del player["skin"]["skinSp"][skin_id]
            changed_paths.add(f"skin.skinSp.{skin_id}")
        if default_skin not in player["skin"]["characterSkins"]:
            player["skin"]["characterSkins"][default_skin] = 1
            changed_paths.add(f"skin.characterSkins.{default_skin}")
        if default_skin not in player["skin"]["skinTs"]:
            player["skin"]["skinTs"][default_skin] = timestamp
            changed_paths.add(f"skin.skinTs.{default_skin}")

    archive = backup_save(save_dir)
    tmp_path = delta_path.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(delta_obj, ensure_ascii=False, indent=4), encoding="utf-8"
    )
    tmp_path.replace(delta_path)
    return archive, {**report, "changedPaths": sorted(changed_paths)}


def main():
    parser = argparse.ArgumentParser(description="Audit post_tutorial operator outfits")
    parser.add_argument(
        "action", choices=("audit", "repair", "revoke-legacy-outfits")
    )
    parser.add_argument("--username")
    parser.add_argument("--single-user", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    save_dir = save_dir_for(args.username, args.single_user)
    if args.action == "audit":
        print(json.dumps(audit_save(save_dir), ensure_ascii=False, indent=2))
        return
    if args.action == "revoke-legacy-outfits":
        archive, report = revoke_legacy_outfits(save_dir, yes=args.yes)
        print(f"backup: {archive}")
        print(json.dumps({"changedPaths": report["changedPaths"]}, ensure_ascii=False, indent=2))
        return
    archive, report = repair_save(save_dir, yes=args.yes)
    print(f"backup: {archive}")
    print(json.dumps({"changedPaths": report["changedPaths"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
