import pytest
from pathlib import Path

from openbachelors.util.player_data import (
    DeltaJson,
    FileBasedDeltaJson,
    OverlayJson,
    post_tutorial_player_data_template,
    sandbox_player_data_template,
)
from openbachelors.util.player_preset import (
    POST_TUTORIAL_COMPLETED_STAGES,
    POST_TUTORIAL_GUIDE_MISSIONS,
    build_post_tutorial_reward_ledger,
)


def test_sandbox_remains_fully_unlocked():
    assert len(sandbox_player_data_template["troop"]["chars"]) > 400
    assert len(sandbox_player_data_template["dungeon"]["stages"]) > 3000
    assert all(
        char["potentialRank"] == 5
        for _, char in sandbox_player_data_template["troop"]["chars"]
    )


def test_post_tutorial_structure_and_progress():
    preset = post_tutorial_player_data_template
    assert 1 < len(preset["troop"]["chars"]) < 50
    assert set(preset["dungeon"]["stages"].copy()) == set(
        POST_TUTORIAL_COMPLETED_STAGES
    )
    assert preset["dungeon"]["stages"]["main_01-10"]["state"] == 3
    assert "main_01-11" not in preset["dungeon"]["stages"]
    assert not all(
        char["potentialRank"] == 5 for _, char in preset["troop"]["chars"]
    )
    assert not all(char["level"] >= 40 for _, char in preset["troop"]["chars"])
    assert preset["troop"]["spOperator"].copy() == {}
    assert preset["mission"]["pinnedSpecialOperator"] is None


def test_post_tutorial_references_are_closed():
    preset = post_tutorial_player_data_template
    chars = preset["troop"]["chars"]
    char_groups = preset["troop"]["charGroup"]
    building_chars = preset["building"]["chars"]
    max_inst_id = 0
    for _, squad in preset["troop"]["squads"]:
        for _, slot in squad["slots"]:
            if slot is None:
                continue
            inst_id = str(slot["charInstId"])
            assert inst_id in chars
            max_inst_id = max(max_inst_id, slot["charInstId"])
    for inst_id, char in chars:
        assert char["charId"] in char_groups
        assert inst_id in building_chars
        max_inst_id = max(max_inst_id, int(inst_id))
    assert preset["troop"]["curCharInstId"] > max_inst_id


def test_completed_missions_have_rewards_and_claimed_state():
    preset = post_tutorial_player_data_template
    ledger, unresolved = build_post_tutorial_reward_ledger()
    assert not unresolved
    assert ledger
    for mission_id in POST_TUTORIAL_GUIDE_MISSIONS:
        assert preset["mission"]["missions"]["GUIDE"][mission_id]["status"] == 3
        assert any(row["source"] == f"mission:{mission_id}" for row in ledger)
    assert "guide_19" not in preset["mission"]["missions"]["GUIDE"]
    assert preset["mission"]["missionGroups"]["guide_g_1"]["state"] == 3
    assert preset["mission"]["missionGroups"]["guide_g_2"]["state"] == 3
    assert "guide_g_3" not in preset["mission"]["missionGroups"]


def test_reward_assets_match_ledger():
    preset = post_tutorial_player_data_template
    assert preset["status"]["gold"] == 70000
    assert preset["status"]["androidDiamond"] == 31
    assert preset["status"]["gachaTicket"] == 2
    assert preset["status"]["recruitLicense"] == 5
    assert preset["status"]["instantFinishTicket"] == 2
    assert preset["inventory"].copy() == {
        "2001": 130,
        "2002": 10,
        "3003": 5,
        "3105": 2,
        "3112": 1,
        "3131": 2,
        "3301": 12,
    }


@pytest.mark.asyncio(loop_scope="session")
async def test_save_reload_does_not_reapply_rewards():
    delta_path = Path("test/.post_tutorial_delta_test.json")
    if delta_path.exists():
        delta_path.unlink()
    delta = DeltaJson()
    player = OverlayJson(post_tutorial_player_data_template, delta)
    initial_gold = player["status"]["gold"]
    player["status"]["gold"] -= 1234
    # Persist the actual delta object, then reload it over the immutable preset.
    persisted = FileBasedDeltaJson()
    persisted.path = str(delta_path)
    persisted.modified_dict = delta.modified_dict
    persisted.deleted_dict = delta.deleted_dict
    try:
        await persisted.save()
        reloaded_delta = await FileBasedDeltaJson.create(str(delta_path))
        reloaded = OverlayJson(post_tutorial_player_data_template, reloaded_delta)
        assert reloaded["status"]["gold"] == initial_gold - 1234
        assert reloaded["status"]["gold"] != initial_gold * 2
    finally:
        if delta_path.exists():
            delta_path.unlink()
