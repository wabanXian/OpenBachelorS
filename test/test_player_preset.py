import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from starlette.requests import Request

from openbachelors.bp import bp_account
from openbachelors.const.filepath import MISSION_TABLE, STAGE_TABLE, TMPL_JSON
from openbachelors.util import player_data as player_data_module
from openbachelors.util.const_json_loader import const_json_loader
from openbachelors.util.player_data import (
    DeltaJson,
    FileBasedDeltaJson,
    OverlayJson,
    PlayerData,
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
    assert len(preset["troop"]["chars"]) == 15


def test_post_tutorial_preserves_special_operator_protocol_contract():
    raw = const_json_loader[TMPL_JSON]
    preset = post_tutorial_player_data_template

    pinned = preset["mission"]["pinnedSpecialOperator"]
    assert isinstance(pinned, str)
    assert pinned in preset["troop"]["spOperator"]
    assert preset["troop"]["spOperator"].copy() == raw["troop"][
        "spOperator"
    ].copy()


def test_post_tutorial_template_types_match_raw_account_contract():
    raw = const_json_loader[TMPL_JSON].copy()
    preset = post_tutorial_player_data_template.copy()
    mismatches = []
    missing = []

    def compare(reference, candidate, path="$"):
        if isinstance(reference, dict):
            assert isinstance(candidate, dict), path
            for key, value in reference.items():
                child_path = f"{path}.{key}"
                if key not in candidate:
                    missing.append(child_path)
                else:
                    compare(value, candidate[key], child_path)
        elif isinstance(reference, list):
            if not isinstance(candidate, list):
                mismatches.append((path, type(reference), type(candidate)))
        elif type(reference) is not type(candidate):
            mismatches.append((path, type(reference), type(candidate)))

    compare(raw, preset)
    assert missing == []
    assert mismatches == [
        ("$.status.mainStageProgress", type(None), str),
    ]


def test_post_tutorial_progress_missions_and_operator_loadouts_are_valid():
    preset = post_tutorial_player_data_template.copy()
    sandbox = sandbox_player_data_template.copy()
    stage_table = const_json_loader[STAGE_TABLE].copy()
    mission_table = const_json_loader[MISSION_TABLE].copy()

    progress = preset["status"]["mainStageProgress"]
    assert progress in stage_table["stages"]
    assert preset["dungeon"]["stages"][progress]["state"] == 3

    for mission_type, missions in preset["mission"]["missions"].items():
        for mission_id, state in missions.items():
            assert mission_table["missions"][mission_id]["type"] == mission_type
            assert set(state) == {"value", "target", "status"}
            assert all(type(state[key]) is int for key in state)
            assert state["status"] == 3

    for group_id, state in preset["mission"]["missionGroups"].items():
        assert group_id in mission_table["missionGroups"]
        assert type(state) is int
        assert state == 3

    for inst_id, char in preset["troop"]["chars"].items():
        source = sandbox["troop"]["chars"][inst_id]
        assert set(char) == set(source)
        assert [skill["skillId"] for skill in char["skills"]] == [
            skill["skillId"] for skill in source["skills"]
        ]
        assert set(char.get("tmpl", {})) == set(source.get("tmpl", {}))
        assert char.get("currentTmpl") == source.get("currentTmpl")
        if char.get("currentTmpl") is not None:
            assert char["currentTmpl"] in char["tmpl"]
        for tmpl_id, loadout in char.get("tmpl", {}).items():
            assert [skill["skillId"] for skill in loadout["skills"]] == [
                skill["skillId"] for skill in source["tmpl"][tmpl_id]["skills"]
            ]


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
    assert len(ledger) == 99
    for mission_id in POST_TUTORIAL_GUIDE_MISSIONS:
        assert preset["mission"]["missions"]["GUIDE"][mission_id]["status"] == 3
        assert any(row["source"] == f"mission:{mission_id}" for row in ledger)
    assert "guide_19" not in preset["mission"]["missions"]["GUIDE"]
    assert preset["mission"]["missionGroups"]["guide_g_1"] == 3
    assert preset["mission"]["missionGroups"]["guide_g_2"] == 3
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


@pytest.mark.asyncio(loop_scope="session")
async def test_sync_data_response_preserves_post_tutorial_contract(
    monkeypatch,
):
    """Exercise the serialized endpoint payload, not only the preset builder."""
    save_root = Path("test") / f".sync_contract_{uuid4().hex}"
    monkeypatch.setattr(
        player_data_module, "MULTI_USER_SAV_DIRPATH", str(save_root / "multi_sav")
    )
    create_player_data = PlayerData.create

    async def create_post_tutorial_player(*, request):
        return await create_player_data(request=request, preset="post_tutorial")

    monkeypatch.setattr(bp_account.PlayerData, "create", create_post_tutorial_player)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/account/syncData",
        "headers": [(b"secret", b"post-tutorial-contract-test")],
    }
    try:
        response = await bp_account.account_syncData(Request(scope))
        payload = json.loads(response.body)
        user = payload["user"]

        assert payload["result"] == 0
        assert payload["playerDataDelta"] == {"modified": {}, "deleted": {}}
        assert len(user["troop"]["chars"]) == 15
        assert user["status"]["mainStageProgress"] == "main_01-10"
        assert len(user["mission"]["missions"]["GUIDE"]) == 18
        assert user["mission"]["missionGroups"] == {
            "guide_g_1": 3,
            "guide_g_2": 3,
        }
        pinned = user["mission"]["pinnedSpecialOperator"]
        assert isinstance(pinned, str)
        assert pinned in user["troop"]["spOperator"]
    finally:
        shutil.rmtree(save_root, ignore_errors=True)
