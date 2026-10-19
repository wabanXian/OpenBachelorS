import json
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from openbachelors.tool.mainline_unlock_save import audit_save, repair_save
from openbachelors.util.battle_settlement import _newly_unlocked, _stage_record
from openbachelors.util.mainline_unlock import implicit_mainline_unlock_conditions
from openbachelors.util.player_data import (
    DeltaJson,
    OverlayJson,
    post_tutorial_player_data_template,
)


EXPECTED_BOUNDARIES = {
    "main_01-01": "main_00-11",
    "tr_11": "main_01-12",
    "main_03-01": "main_02-10",
    "main_04-01": "main_03-08",
    "main_05-01": "main_04-10",
    "main_06-01": "st_05-01",
    "st_07-01": "st_06-03",
    "main_08-01": "st_07-04",
    "st_09-01": "st_08-06",
    "st_10-01": "st_09-04",
    "st_12-01": "st_11-03",
    "st_13-01": "st_12-03",
    "st_14-01": "st_13-03",
}


def fresh_player():
    return OverlayJson(post_tutorial_player_data_template, DeltaJson())


def test_resource_derived_mainline_boundaries_are_exact_and_exclude_activities():
    actual = {
        target: condition["stageId"]
        for target, condition in implicit_mainline_unlock_conditions().items()
    }
    assert actual == EXPECTED_BOUNDARIES
    assert "main_00-01" not in actual
    assert "st_15-01" not in actual
    assert "st_16-01" not in actual
    assert "st_17-01" not in actual


@pytest.mark.parametrize(("target", "predecessor"), EXPECTED_BOUNDARIES.items())
def test_mainline_boundary_requires_predecessor_pass(target, predecessor):
    value = fresh_player()
    value["dungeon"]["stages"][predecessor] = _stage_record(predecessor, 0)
    assert target not in _newly_unlocked(value, {})
    assert target in _newly_unlocked(value, {predecessor: 2})
    assert target in _newly_unlocked(value, {predecessor: 3})


def test_tr11_then_explicit_two_one_chain_remains_intact():
    value = fresh_player()
    value["dungeon"]["stages"]["main_01-12"] = _stage_record("main_01-12", 3)
    assert "tr_11" in _newly_unlocked(value, {})
    value["dungeon"]["stages"]["tr_11"] = _stage_record("tr_11", 3)
    assert "main_02-01" in _newly_unlocked(value, {})


def test_non_mainline_empty_condition_is_not_implicitly_unlocked():
    value = fresh_player()
    value["dungeon"]["stages"]["st_14-03"] = _stage_record("st_14-03", 3)
    unlocked = _newly_unlocked(value, {})
    assert "st_15-01" not in unlocked


def test_legacy_save_repair_is_backed_up_minimal_persistent_and_idempotent(monkeypatch):
    root = Path("test") / f".mainline_unlock_{uuid4().hex}"
    save_dir = (root / "multi_sav" / "presets" / "post_tutorial" / "copy").resolve()
    save_dir.mkdir(parents=True)
    player = fresh_player()
    player["dungeon"]["stages"]["main_00-11"] = _stage_record("main_00-11", 3)
    player["dungeon"]["stages"]["main_00-11"]["completeTimes"] = 1
    player["status"]["mainStageProgress"] = "main_00-11"
    player["status"]["gold"] = 12345
    player["inventory"]["2001"] = 9
    delta_obj = {
        "modified": player.delta_json.modified_dict,
        "deleted": player.delta_json.deleted_dict,
    }
    (save_dir / "delta.json").write_text(json.dumps(delta_obj), encoding="utf-8")
    (save_dir / "pending_delta.json").write_text(
        json.dumps({"modified": {}, "deleted": {}}), encoding="utf-8"
    )
    (save_dir / "extra.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(root)
    try:
        preview = audit_save(save_dir)
        assert [item["targetStageId"] for item in preview["repairCandidates"]] == [
            "main_01-01"
        ]
        with pytest.raises(RuntimeError, match="without --yes"):
            repair_save(save_dir)

        archive, report = repair_save(save_dir, yes=True)
        assert archive.is_file()
        assert report["changedPaths"] == ["dungeon.stages.main_01-01"]
        _, delta_after, _, reloaded = __import__(
            "openbachelors.tool.mainline_unlock_save", fromlist=["_load"]
        )._load(save_dir)
        assert reloaded["dungeon"]["stages"]["main_01-01"].copy() == _stage_record(
            "main_01-01"
        )
        assert reloaded["dungeon"]["stages"]["main_00-11"]["state"] == 3
        assert reloaded["status"]["mainStageProgress"] == "main_00-11"
        assert reloaded["status"]["gold"] == 12345
        assert reloaded["inventory"]["2001"] == 9
        assert delta_after["modified"]["dungeon"]["stages"]["main_01-01"]["state"] == 0

        archive_again, repeated = repair_save(save_dir, yes=True)
        assert archive_again is None
        assert repeated["changedPaths"] == []
    finally:
        monkeypatch.undo()
        shutil.rmtree(root, ignore_errors=True)
