import pytest
from fastapi import HTTPException

from openbachelors.bp.bp_charBuild import (
    charBuild_addonStage_battleStart,
    charBuild_setEquipment,
    charBuild_unlockEquipment,
    charBuild_upgradeEquipment,
    router,
)
from openbachelors.util.player_data import DeltaJson
from openbachelors.util.favor import build_squad_favor_snapshot

from .test_char_build_evolve_skill import add_costs, make_player, make_request


EQUIP = "uniequip_002_amiya"


def prepare_amiya_module(player, missions=True):
    char = player["troop"]["chars"]["2"]
    char["evolvePhase"] = 2
    char["level"] = 50
    char["favorPoint"] = 10070
    loadout = char["tmpl"]["char_002_amiya"]
    loadout["equip"] = {}
    loadout["currentEquip"] = None
    if missions:
        player["equipment"]["missions"]["uniequip_002_amiya_1"] = {"value": 10, "target": 10}
        player["equipment"]["missions"]["uniequip_002_amiya_2"] = {"value": 1, "target": 1}
    return char, loadout


def request(path, **payload):
    return make_request(path, payload)


def test_module_routes_are_registered():
    paths = {route.path for route in router.routes if "POST" in route.methods}
    assert {"/charBuild/unlockEquipment", "/charBuild/upgradeEquipment", "/charBuild/setEquipment"} <= paths


@pytest.mark.asyncio
async def test_unlock_module_checks_missions_and_consumes_level_one_cost():
    player = make_player()
    _, loadout = prepare_amiya_module(player, missions=False)
    add_costs(player, {"mod_unlock_token": 2, "30034": 3})
    player["status"]["gold"] = 40000
    payload = {"charInstId": 2, "templateId": "char_002_amiya", "equipId": EQUIP}

    before = player.copy()
    with pytest.raises(HTTPException, match="module_missions_incomplete"):
        await charBuild_unlockEquipment.__wrapped__(player, request("/charBuild/unlockEquipment", **payload))
    assert player.copy() == before

    player["equipment"]["missions"]["uniequip_002_amiya_1"] = {"value": 10, "target": 10}
    player["equipment"]["missions"]["uniequip_002_amiya_2"] = {"value": 1, "target": 1}
    await charBuild_unlockEquipment.__wrapped__(player, request("/charBuild/unlockEquipment", **payload))
    assert loadout["equip"][EQUIP].copy() == {"hide": 0, "locked": 0, "level": 1}
    assert player["inventory"]["mod_unlock_token"] == 0
    assert player["inventory"]["30034"] == 0
    assert player["status"]["gold"] == 0

    after = player.copy()
    with pytest.raises(HTTPException, match="module_already_unlocked"):
        await charBuild_unlockEquipment.__wrapped__(
            player, request("/charBuild/unlockEquipment", **payload)
        )
    assert player.copy() == after


@pytest.mark.asyncio
async def test_module_upgrade_two_and_three_uses_exact_costs_and_favor():
    player = make_player()
    char, loadout = prepare_amiya_module(player)
    loadout["equip"] = {EQUIP: {"hide": 0, "locked": 0, "level": 1}}
    player["status"]["gold"] = 110000
    add_costs(player, {"mod_unlock_token": 4, "mod_update_token_1": 20, "30054": 4, "mod_update_token_2": 8, "30044": 5})

    for target in (2, 3):
        await charBuild_upgradeEquipment.__wrapped__(
            player,
            request("/charBuild/upgradeEquipment", charInstId=2, templateId="char_002_amiya", equipId=EQUIP, targetLevel=target),
        )
        assert loadout["equip"][EQUIP]["level"] == target
    assert player["status"]["gold"] == 0
    assert player["inventory"]["mod_unlock_token"] == 0
    assert player["inventory"]["mod_update_token_1"] == 0
    assert player["inventory"]["mod_update_token_2"] == 0

    before = player.copy()
    with pytest.raises(HTTPException, match="module_at_level_cap"):
        await charBuild_upgradeEquipment.__wrapped__(
            player,
            request("/charBuild/upgradeEquipment", charInstId=2, templateId="char_002_amiya", equipId=EQUIP, targetLevel=4),
        )
    assert player.copy() == before


@pytest.mark.asyncio
async def test_module_equip_unequip_and_cross_operator_rejection():
    player = make_player()
    _, loadout = prepare_amiya_module(player)
    loadout["equip"] = {EQUIP: {"hide": 0, "locked": 0, "level": 1}}

    await charBuild_setEquipment.__wrapped__(player, request("/charBuild/setEquipment", charInstId=2, templateId="char_002_amiya", equipId=EQUIP))
    assert loadout["currentEquip"] == EQUIP
    await charBuild_setEquipment.__wrapped__(player, request("/charBuild/setEquipment", charInstId=2, templateId="char_002_amiya", equipId=None))
    assert loadout["currentEquip"] is None

    before = player.copy()
    with pytest.raises(HTTPException, match="module_not_compatible"):
        await charBuild_setEquipment.__wrapped__(player, request("/charBuild/setEquipment", charInstId=2, templateId="char_002_amiya", equipId="uniequip_002_doberm"))
    assert player.copy() == before


def test_battle_squad_rejects_locked_module_and_preserves_unlocked_selection():
    player = make_player()
    _, loadout = prepare_amiya_module(player)
    slot = {
        "charInstId": 2,
        "currentTmpl": "char_002_amiya",
        "tmpl": {"char_002_amiya": {"skillIndex": 0, "currentEquip": EQUIP}},
    }
    snapshot, error = build_squad_favor_snapshot(player, {"squad": {"slots": [slot]}})
    assert snapshot is None
    assert error == "squad_module_not_unlocked"

    loadout["equip"] = {EQUIP: {"hide": 0, "locked": 0, "level": 1}}
    snapshot, error = build_squad_favor_snapshot(player, {"squad": {"slots": [slot]}})
    assert error is None
    assert snapshot[0]["currentEquip"] == EQUIP


@pytest.mark.asyncio
async def test_module_failures_are_atomic_and_reload_persists():
    delta = DeltaJson()
    player = make_player(delta, {})
    char, loadout = prepare_amiya_module(player)
    add_costs(player, {"mod_unlock_token": 2, "30034": 3})
    player["status"]["gold"] = 40000
    await charBuild_unlockEquipment.__wrapped__(player, request("/charBuild/unlockEquipment", charInstId=2, templateId="char_002_amiya", equipId=EQUIP))

    char["favorPoint"] = 0
    before = player.copy()
    with pytest.raises(HTTPException, match="module_favor_condition_not_met"):
        await charBuild_upgradeEquipment.__wrapped__(player, request("/charBuild/upgradeEquipment", charInstId=2, templateId="char_002_amiya", equipId=EQUIP, targetLevel=2))
    assert player.copy() == before

    reloaded = make_player(delta, {})
    saved = reloaded["troop"]["chars"]["2"]["tmpl"]["char_002_amiya"]["equip"][EQUIP]
    assert saved.copy() == {"hide": 0, "locked": 0, "level": 1}


@pytest.mark.asyncio
async def test_unverified_addon_stage_is_not_reported_as_success():
    player = make_player()
    before = player.copy()
    with pytest.raises(HTTPException, match="addon_stage_settlement_not_verified"):
        await charBuild_addonStage_battleStart.__wrapped__(
            player,
            request(
                "/charBuild/addonStage/battleStart",
                charId="char_002_amiya",
                stageId="uniequip_002_amiya_1",
                squad={},
                stageType=0,
            ),
        )
    assert player.copy() == before
