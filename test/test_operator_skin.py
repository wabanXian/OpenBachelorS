import json
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from openbachelors.bp.bp_gacha import grant_gacha_character
from openbachelors.tool.operator_skin_save import audit_save, repair_save
from openbachelors.util.operator_skin import (
    default_evolve_skin,
    default_template_skin,
    equipped_skin_id,
)
from openbachelors.util.player_data import (
    DeltaJson,
    OverlayJson,
    post_tutorial_player_data_template,
    sandbox_player_data_template,
)


def fresh_player():
    return OverlayJson(post_tutorial_player_data_template, DeltaJson())


def test_initial_operators_use_resource_e0_skins_and_own_only_equipped_defaults():
    player = fresh_player()
    owned = player["skin"]["characterSkins"].copy()
    equipped = set()
    for _, char_value in player["troop"]["chars"]:
        char = char_value.copy()
        assert int(char["evolvePhase"]) == 0
        current_tmpl = char.get("currentTmpl")
        if current_tmpl:
            assert char.get("skin") is None
            for tmpl_id, loadout in char.get("tmpl", {}).items():
                assert loadout["skinId"] == default_template_skin(
                    char["charId"], tmpl_id, 0
                )
        else:
            assert char["skin"] == default_evolve_skin(char["charId"], 0)
        equipped.add(equipped_skin_id(char))
    assert set(owned) == equipped


@pytest.mark.parametrize(
    "char_id,forbidden",
    (
        ("char_208_melan", "char_208_melan@epoque#1"),
        ("char_120_hibisc", "char_120_hibisc@nian#1"),
        ("char_211_adnach", "char_211_adnach@sale#5"),
    ),
)
def test_gacha_and_recruit_grants_use_resource_e0_skin(char_id, forbidden):
    player = fresh_player()
    result = grant_gacha_character(player, char_id)
    char = player["troop"]["chars"][str(result["charInstId"])]
    assert char["evolvePhase"] == 0
    assert char["skin"] == default_evolve_skin(char_id, 0)
    assert char["skin"] != forbidden
    assert char["skin"] in player["skin"]["characterSkins"]
    assert forbidden not in player["skin"]["characterSkins"]


def test_patch_operator_templates_use_builtin_patch_map_not_sandbox_outfits():
    amiya = next(
        char for _, char in fresh_player()["troop"]["chars"]
        if char["charId"] == "char_002_amiya"
    )
    assert amiya["skin"] is None
    assert amiya["tmpl"]["char_002_amiya"]["skinId"] == "char_002_amiya#1"
    assert amiya["tmpl"]["char_1001_amiya2"]["skinId"] == "char_1001_amiya2#2"
    assert amiya["tmpl"]["char_1037_amiya3"]["skinId"] == "char_1037_amiya3#2"


def test_evolve_skin_map_is_phase_specific_and_does_not_select_outfits():
    assert default_evolve_skin("char_002_amiya", 0) == "char_002_amiya#1"
    assert default_evolve_skin("char_002_amiya", 1) == "char_002_amiya#1+"
    assert default_evolve_skin("char_002_amiya", 2) == "char_002_amiya#2"


def test_existing_player_selected_skin_is_not_normalized_on_reload():
    delta = DeltaJson()
    player = OverlayJson(post_tutorial_player_data_template, delta)
    result = grant_gacha_character(player, "char_120_hibisc")
    inst_id = str(result["charInstId"])
    selected = "char_120_hibisc@nian#1"
    player["skin"]["characterSkins"][selected] = 1
    player["skin"]["skinTs"][selected] = 1800000000
    player["troop"]["chars"][inst_id]["skin"] = selected

    reloaded = OverlayJson(post_tutorial_player_data_template, delta)
    assert reloaded["troop"]["chars"][inst_id]["skin"] == selected


def test_sandbox_keeps_its_original_equipped_outfits():
    hibiscus = next(
        char for _, char in sandbox_player_data_template["troop"]["chars"]
        if char["charId"] == "char_120_hibisc"
    )
    assert hibiscus["skin"] == "char_120_hibisc@nian#1"


def test_skin_save_audit_and_explicit_repair_are_minimal(monkeypatch):
    root = Path("test") / f".operator_skin_repair_{uuid4().hex}"
    save_dir = (root / "multi_sav" / "presets" / "post_tutorial" / "copy").resolve()
    save_dir.mkdir(parents=True)
    player = fresh_player()
    result = grant_gacha_character(player, "char_120_hibisc")
    inst_id = str(result["charInstId"])
    wrong = "char_120_hibisc@nian#1"
    player["troop"]["chars"][inst_id]["skin"] = wrong
    player["skin"]["characterSkins"][wrong] = 1
    player["skin"]["skinTs"][wrong] = 1700000000
    player["status"]["progress"] = 1111
    player["status"]["gold"] = 14587
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
        report = audit_save(save_dir)
        assert report["repairCandidateOperatorCount"] == 1
        assert report["repairCandidates"][0]["actual"] == wrong
        with pytest.raises(RuntimeError, match="without --yes"):
            repair_save(save_dir)

        archive, repaired = repair_save(save_dir, yes=True)
        assert archive.is_file()
        assert repaired["changedPaths"] == [f"troop.chars.{inst_id}.skin"]
        final = audit_save(save_dir)
        assert final["repairCandidateOperatorCount"] == 0
        raw = json.loads((save_dir / "delta.json").read_text(encoding="utf-8"))
        assert raw["modified"]["status"]["progress"] == 1111
        assert raw["modified"]["status"]["gold"] == 14587
        assert raw["modified"]["skin"]["characterSkins"][wrong] == 1
        assert raw["modified"]["skin"]["skinTs"][wrong] == 1700000000
    finally:
        monkeypatch.undo()
        shutil.rmtree(root, ignore_errors=True)
