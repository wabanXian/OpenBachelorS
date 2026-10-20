import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from openbachelors.bp.bp_charBuild import (
    charBuild_evolveChar,
    charBuild_upgradeSkill,
    router as char_build_router,
)
from openbachelors.util.player_data import (
    DeltaJson,
    OverlayJson,
    post_tutorial_player_data_template,
    sandbox_player_data_template,
)
from openbachelors.util.player_preset import _normalise_char


class FakeExtra:
    def __init__(self, save_obj=None):
        self.save_obj = save_obj if save_obj is not None else {}

    async def save(self):
        return None


def make_player(delta=None, extra=None):
    player = OverlayJson(post_tutorial_player_data_template, delta or DeltaJson())
    player.extra_save = FakeExtra(extra)
    return player


def make_request(path, payload):
    body = json.dumps(payload).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )


def add_costs(player, costs):
    for item_id, count in costs.items():
        player["inventory"][item_id] = count


def add_normalised_char(player, char_id, inst_id=999):
    source = next(
        value.copy()
        for _, value in sandbox_player_data_template["troop"]["chars"]
        if value["charId"] == char_id
    )
    char = _normalise_char(source)
    char["instId"] = inst_id
    player["troop"]["chars"][str(inst_id)] = char
    return player["troop"]["chars"][str(inst_id)]


def prepare_amiya_evolve(player, phase):
    char = player["troop"]["chars"]["2"]
    char["evolvePhase"] = phase
    char["level"] = (50, 70)[phase]
    char["exp"] = 0
    if phase == 1:
        char["tmpl"]["char_002_amiya"]["skinId"] = "char_002_amiya#1+"
        skills = char["tmpl"]["char_002_amiya"]["skills"].copy()
        skills[1]["unlock"] = 1
        char["tmpl"]["char_002_amiya"]["skills"] = skills
    return char


def evolve_request(dest):
    return make_request(
        "/charBuild/evolveChar", {"charInstId": 2, "destEvolvePhase": dest}
    )


def skill_request(target):
    return make_request(
        "/charBuild/upgradeSkill", {"charInstId": 2, "targetLevel": target}
    )


def test_routes_are_registered():
    paths = {route.path for route in char_build_router.routes if "POST" in route.methods}
    assert "/charBuild/evolveChar" in paths
    assert "/charBuild/upgradeSkill" in paths


@pytest.mark.asyncio
async def test_e0_to_e1_uses_resource_costs_unlocks_skill_and_skin():
    player = make_player()
    char = prepare_amiya_evolve(player, 0)
    player["status"]["gold"] = 30000
    add_costs(player, {"3251": 3, "30062": 4, "30042": 4})

    response = await charBuild_evolveChar.__wrapped__(player, evolve_request(1))

    assert response == {}
    assert (char["evolvePhase"], char["level"], char["exp"]) == (1, 1, 0)
    assert player["status"]["gold"] == 10000
    assert {item: player["inventory"][item] for item in ("3251", "30062", "30042")} == {
        "3251": 0,
        "30062": 0,
        "30042": 0,
    }
    loadout = char["tmpl"]["char_002_amiya"]
    assert [row["unlock"] for _, row in loadout["skills"]] == [1, 1, 0]
    assert loadout["skinId"] == "char_002_amiya#1+"
    assert player["skin"]["characterSkins"]["char_002_amiya#1+"] == 1


@pytest.mark.asyncio
async def test_e1_to_e2_persists_and_does_not_unlock_other_templates():
    delta, extra = DeltaJson(), {}
    player = make_player(delta, extra)
    char = prepare_amiya_evolve(player, 1)
    player["status"]["gold"] = 150000
    add_costs(player, {"3253": 3, "30014": 10, "30073": 10})
    other_before = char["tmpl"]["char_1001_amiya2"].copy()

    await charBuild_evolveChar.__wrapped__(player, evolve_request(2))

    assert (char["evolvePhase"], char["level"], char["exp"]) == (2, 1, 0)
    assert player["status"]["gold"] == 30000
    assert [row["unlock"] for _, row in char["tmpl"]["char_002_amiya"]["skills"]] == [1, 1, 1]
    assert char["tmpl"]["char_1001_amiya2"].copy() == other_before
    reloaded = make_player(delta, extra)
    assert reloaded["troop"]["chars"]["2"]["evolvePhase"] == 2
    assert reloaded["troop"]["chars"]["2"]["tmpl"]["char_002_amiya"]["skinId"] == "char_002_amiya#2"


@pytest.mark.asyncio
async def test_evolve_preserves_player_selected_skin():
    player = make_player()
    char = prepare_amiya_evolve(player, 0)
    char["tmpl"]["char_002_amiya"]["skinId"] = "char_002_amiya@test#1"
    player["status"]["gold"] = 20000
    add_costs(player, {"3251": 3, "30062": 4, "30042": 4})

    await charBuild_evolveChar.__wrapped__(player, evolve_request(1))

    assert char["tmpl"]["char_002_amiya"]["skinId"] == "char_002_amiya@test#1"
    assert player["skin"]["characterSkins"]["char_002_amiya#1+"] == 1


@pytest.mark.asyncio
async def test_evolve_rejects_level_material_gold_and_unsupported_phase_atomically():
    player = make_player()
    char = player["troop"]["chars"]["2"]
    char["level"] = 49
    before = player.copy()
    with pytest.raises(HTTPException, match="character_not_at_phase_cap"):
        await charBuild_evolveChar.__wrapped__(player, evolve_request(1))
    assert player.copy() == before

    char["level"] = 50
    before = player.copy()
    with pytest.raises(HTTPException, match="insufficient_gold"):
        await charBuild_evolveChar.__wrapped__(player, evolve_request(1))
    assert player.copy() == before

    player["status"]["gold"] = 20000
    before = player.copy()
    with pytest.raises(HTTPException, match="insufficient_material"):
        await charBuild_evolveChar.__wrapped__(player, evolve_request(1))
    assert player.copy() == before

    melan = add_normalised_char(player, "char_208_melan")
    melan["evolvePhase"], melan["level"] = 1, 55
    req = make_request("/charBuild/evolveChar", {"charInstId": 999, "destEvolvePhase": 2})
    before = player.copy()
    with pytest.raises(HTTPException, match="unsupported_evolve_phase"):
        await charBuild_evolveChar.__wrapped__(player, req)
    assert player.copy() == before


@pytest.mark.asyncio
async def test_ordinary_operator_evolves_and_upgrades_skill_without_template_data():
    player = make_player()
    melan = add_normalised_char(player, "char_208_melan")
    melan["level"] = 40
    player["status"]["gold"] = 10000
    evolve = make_request(
        "/charBuild/evolveChar", {"charInstId": 999, "destEvolvePhase": 1}
    )

    await charBuild_evolveChar.__wrapped__(player, evolve)
    assert (melan["evolvePhase"], melan["level"], melan["exp"]) == (1, 1, 0)
    assert melan["skills"][0]["unlock"] == 1

    player["inventory"]["3301"] = 1
    skill = make_request(
        "/charBuild/upgradeSkill", {"charInstId": 999, "targetLevel": 2}
    )
    await charBuild_upgradeSkill.__wrapped__(player, skill)
    assert melan["mainSkillLvl"] == 2
    assert player["inventory"]["3301"] == 0


@pytest.mark.asyncio
async def test_evolve_updates_visible_state_mission_and_replay_is_idempotent():
    extra = {}
    player = make_player(extra=extra)
    prepare_amiya_evolve(player, 0)
    player["status"]["gold"] = 20000
    add_costs(player, {"3251": 3, "30062": 4, "30042": 4})
    player["mission"]["missionGroups"]["guide_g_4"] = 0
    player["dungeon"]["stages"]["main_01-08"] = {
        "stageId": "main_01-08",
        "state": 2,
        "completeTimes": 1,
        "startTimes": 1,
        "practiceTimes": 0,
        "hasBattleReplay": 0,
        "noCostCnt": 0,
    }
    player["mission"]["missions"]["GUIDE"]["guide_28"] = {
        "progress": [{"target": 1, "value": 0}],
        "state": 0,
    }

    await charBuild_evolveChar.__wrapped__(player, evolve_request(1))
    after = player.copy()
    assert player["mission"]["missions"]["GUIDE"]["guide_28"]["progress"][0]["value"] == 1
    assert player["mission"]["missions"]["GUIDE"]["guide_28"]["state"] == 2

    await charBuild_evolveChar.__wrapped__(player, evolve_request(1))
    assert player.copy() == after


@pytest.mark.asyncio
async def test_skill_upgrade_1_to_2_uses_exact_materials_and_keeps_specialization():
    player = make_player()
    char = player["troop"]["chars"]["2"]
    player["inventory"]["3301"] = 4
    before_gold = player["status"]["gold"]
    specializations = [row["specializeLevel"] for _, row in char["tmpl"]["char_002_amiya"]["skills"]]

    response = await charBuild_upgradeSkill.__wrapped__(player, skill_request(2))

    assert response == {}
    assert char["mainSkillLvl"] == 2
    assert player["inventory"]["3301"] == 0
    assert player["status"]["gold"] == before_gold
    assert [row["specializeLevel"] for _, row in char["tmpl"]["char_002_amiya"]["skills"]] == specializations


@pytest.mark.asyncio
async def test_skill_upgrade_6_to_7_and_level_cap():
    player = make_player()
    char = player["troop"]["chars"]["2"]
    char["evolvePhase"], char["level"], char["mainSkillLvl"] = 1, 1, 6
    add_costs(player, {"3303": 6, "30063": 2, "30023": 3})

    await charBuild_upgradeSkill.__wrapped__(player, skill_request(7))
    assert char["mainSkillLvl"] == 7

    before = player.copy()
    with pytest.raises(HTTPException, match="skill_at_level_cap"):
        await charBuild_upgradeSkill.__wrapped__(player, skill_request(8))
    assert player.copy() == before


@pytest.mark.asyncio
async def test_skill_upgrade_condition_and_material_failures_are_atomic():
    player = make_player()
    char = player["troop"]["chars"]["2"]
    char["mainSkillLvl"] = 4
    before = player.copy()
    with pytest.raises(HTTPException, match="skill_upgrade_condition_not_met"):
        await charBuild_upgradeSkill.__wrapped__(player, skill_request(5))
    assert player.copy() == before

    char["mainSkillLvl"] = 1
    before = player.copy()
    with pytest.raises(HTTPException, match="insufficient_material"):
        await charBuild_upgradeSkill.__wrapped__(player, skill_request(2))
    assert player.copy() == before


@pytest.mark.asyncio
async def test_skill_upgrade_updates_mission_persists_and_replay_is_idempotent():
    delta, extra = DeltaJson(), {}
    player = make_player(delta, extra)
    player["inventory"]["3301"] = 4
    player["mission"]["missions"]["GUIDE"]["guide_2"] = {
        "progress": [{"target": 1, "value": 0}],
        "state": 0,
    }

    await charBuild_upgradeSkill.__wrapped__(player, skill_request(2))
    after = player.copy()
    assert player["mission"]["missions"]["GUIDE"]["guide_2"]["state"] == 2
    assert player["mission"]["missions"]["GUIDE"]["guide_2"]["progress"][0]["value"] == 1

    reloaded = make_player(delta, extra)
    await charBuild_upgradeSkill.__wrapped__(reloaded, skill_request(2))
    assert reloaded.copy() == after

