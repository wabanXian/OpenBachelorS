import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from starlette.requests import Request

from openbachelors.bp import bp_account
from openbachelors.const.filepath import GAMEDATA_CONST, TMPL_JSON
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
from openbachelors.util.player_preset import build_post_tutorial_reward_ledger


def test_sandbox_remains_fully_unlocked():
    assert len(sandbox_player_data_template["troop"]["chars"]) > 400
    assert len(sandbox_player_data_template["dungeon"]["stages"]) > 3000
    assert all(
        char["potentialRank"] == 5
        for _, char in sandbox_player_data_template["troop"]["chars"]
    )


def test_post_tutorial_is_a_fresh_account_from_resource_constants():
    preset = post_tutorial_player_data_template.copy()
    game_const = const_json_loader[GAMEDATA_CONST].copy()
    owned_char_ids = {char["charId"] for char in preset["troop"]["chars"].values()}

    assert owned_char_ids == set(game_const["initCharIdList"])
    assert preset["status"]["level"] == 1
    assert preset["status"]["exp"] == 0
    assert preset["status"]["ap"] == game_const["playerApMap"][0]
    assert preset["status"]["maxAp"] == game_const["playerApMap"][0]
    assert preset["status"]["gold"] == game_const["initPlayerGold"]
    assert (
        preset["status"]["diamondShard"]
        == game_const["initPlayerDiamondShard"]
    )
    assert preset["status"]["mainStageProgress"] is None
    assert preset["status"]["progress"] == 0
    assert preset["inventory"] == {}


def test_post_tutorial_has_no_precompleted_progress_or_rewards():
    preset = post_tutorial_player_data_template.copy()
    ledger, unresolved = build_post_tutorial_reward_ledger()

    assert preset["dungeon"]["stages"].copy() == {
        "main_00-01": {
            "stageId": "main_00-01",
            "completeTimes": 0,
            "startTimes": 0,
            "practiceTimes": 0,
            "state": 0,
            "hasBattleReplay": 0,
            "noCostCnt": 0,
        }
    }
    assert all(not missions for missions in preset["mission"]["missions"].values())
    assert preset["mission"]["missionGroups"] == {}
    assert preset["status"]["flags"] == {"init": 1}
    assert ledger == []
    assert unresolved == []


def test_initial_operators_are_unupgraded_and_squads_are_empty():
    preset = post_tutorial_player_data_template.copy()
    sandbox = sandbox_player_data_template.copy()

    for inst_id, char in preset["troop"]["chars"].items():
        source = sandbox["troop"]["chars"][inst_id]
        assert char["level"] == 1
        assert char["exp"] == 0
        assert char["evolvePhase"] == 0
        assert char["potentialRank"] == 0
        assert char["mainSkillLvl"] == 1
        assert [skill["skillId"] for skill in char["skills"]] == [
            skill["skillId"] for skill in source["skills"]
        ]
        assert set(char.get("tmpl", {})) == set(source.get("tmpl", {}))
        assert char.get("currentTmpl") == source.get("currentTmpl")

    for squad in preset["troop"]["squads"].values():
        assert squad["slots"] == [None] * 12


def test_post_tutorial_references_are_closed():
    preset = post_tutorial_player_data_template.copy()
    chars = preset["troop"]["chars"]
    max_inst_id = 0

    for inst_id, char in chars.items():
        assert char["charId"] in preset["troop"]["charGroup"]
        assert inst_id in preset["building"]["chars"]
        assert preset["dexNav"]["character"][char["charId"]]["charInstId"] == int(
            inst_id
        )
        if char.get("currentTmpl") is not None:
            assert char["currentTmpl"] in char["tmpl"]
        max_inst_id = max(max_inst_id, int(inst_id))

    assert preset["troop"]["curCharInstId"] > max_inst_id


def test_post_tutorial_preserves_special_operator_protocol_contract():
    raw = const_json_loader[TMPL_JSON]
    preset = post_tutorial_player_data_template
    pinned = preset["mission"]["pinnedSpecialOperator"]

    assert isinstance(pinned, str)
    assert pinned in preset["troop"]["spOperator"]
    assert preset["troop"]["spOperator"].copy() == raw["troop"][
        "spOperator"
    ].copy()


@pytest.mark.asyncio(loop_scope="session")
async def test_save_reload_keeps_fresh_account_progress():
    delta_path = Path("test/.post_tutorial_delta_test.json")
    if delta_path.exists():
        delta_path.unlink()
    delta = DeltaJson()
    player = OverlayJson(post_tutorial_player_data_template, delta)
    initial_gold = player["status"]["gold"]
    player["status"]["gold"] -= 1234
    player["status"]["progress"] = 100
    persisted = FileBasedDeltaJson()
    persisted.path = str(delta_path)
    persisted.modified_dict = delta.modified_dict
    persisted.deleted_dict = delta.deleted_dict
    try:
        await persisted.save()
        reloaded_delta = await FileBasedDeltaJson.create(str(delta_path))
        reloaded = OverlayJson(post_tutorial_player_data_template, reloaded_delta)
        assert reloaded["status"]["gold"] == initial_gold - 1234
        assert reloaded["status"]["progress"] == 100
        assert reloaded["dungeon"]["stages"]["main_00-01"]["state"] == 0
    finally:
        if delta_path.exists():
            delta_path.unlink()


@pytest.mark.asyncio(loop_scope="session")
async def test_sync_data_response_contains_fresh_account(monkeypatch):
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
        "headers": [(b"secret", b"fresh-account-contract-test")],
    }
    try:
        response = await bp_account.account_syncData(Request(scope))
        payload = json.loads(response.body)
        user = payload["user"]

        assert payload["result"] == 0
        assert payload["playerDataDelta"] == {"modified": {}, "deleted": {}}
        assert user["status"]["level"] == 1
        assert user["status"]["mainStageProgress"] is None
        assert user["status"]["lastApAddTime"] == payload["ts"]
        assert user["dungeon"]["stages"] == {
            "main_00-01": {
                "stageId": "main_00-01",
                "completeTimes": 0,
                "startTimes": 0,
                "practiceTimes": 0,
                "state": 0,
                "hasBattleReplay": 0,
                "noCostCnt": 0,
            }
        }
        assert user["mission"]["missionGroups"] == {}
        assert all(not missions for missions in user["mission"]["missions"].values())
    finally:
        shutil.rmtree(save_root, ignore_errors=True)
