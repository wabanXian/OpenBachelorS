import pytest
from fastapi import HTTPException

import openbachelors.bp.bp_charBuild as char_build
from openbachelors.bp.bp_charBuild import (
    charBuild_completeUpgradeSpecialization,
    charBuild_upgradeSpecialization,
)
from openbachelors.util.player_data import DeltaJson

from .test_char_build_evolve_skill import add_costs, make_player, make_request


def prepare_training(player, level=3):
    player["building"]["roomSlots"]["slot_13"] = {
        "level": level,
        "state": 2,
        "roomId": "TRAINING",
        "charInstIds": [-1, -1],
        "completeConstructTime": -1,
    }
    player["building"]["rooms"]["TRAINING"] = {
        "slot_13": {
            "buff": {"speed": 0, "lvEx": {}, "lvCost": {}, "reduce": {"target": None, "progress": 0, "cut": 1}, "reduceTimeBd": {"fulltime": False, "activated": False, "cnt": 0, "reset": False}, "apCost": 0},
            "state": 0,
            "lastUpdateTime": 1700000000,
            "trainee": {"charInstId": -1, "state": 0, "targetSkill": -1, "processPoint": -1, "speed": 1},
            "trainer": {"charInstId": -1, "state": 0},
        }
    }
    player["building"]["chars"]["2"]["roomSlotId"] = ""
    player["building"]["chars"]["2"]["index"] = -1
    char = player["troop"]["chars"]["2"]
    char["evolvePhase"] = 2
    char["level"] = 1
    char["exp"] = 0
    char["mainSkillLvl"] = 7
    skills = char["tmpl"]["char_002_amiya"]["skills"].copy()
    for row in skills:
        row["unlock"] = 1
        row["specializeLevel"] = 0
        row["completeUpgradeTime"] = -1
    char["tmpl"]["char_002_amiya"]["skills"] = skills
    return char


def start_request(skill=0, target=1):
    return make_request("/charBuild/upgradeSpecialization", {"charInstId": 2, "skillIndex": skill, "targetLevel": target})


def complete_request():
    return make_request("/charBuild/completeUpgradeSpecialization", {})


@pytest.mark.asyncio
async def test_specialization_m0_to_m1_consumes_exact_cost_and_observes_timer(monkeypatch):
    player = make_player()
    char = prepare_training(player)
    add_costs(player, {"3303": 5, "30074": 3, "30053": 5})
    monkeypatch.setattr(char_build, "faketime", lambda: 1000)

    await charBuild_upgradeSpecialization.__wrapped__(player, start_request())

    skill = char["tmpl"]["char_002_amiya"]["skills"][0]
    room = player["building"]["rooms"]["TRAINING"]["slot_13"]
    assert skill["specializeLevel"] == 0
    assert skill["completeUpgradeTime"] == 29800
    assert room["trainee"].copy() == {"charInstId": 2, "state": 1, "targetSkill": 0, "processPoint": 0, "speed": 1}
    assert player["building"]["roomSlots"]["slot_13"]["charInstIds"].copy() == [-1, 2]
    assert {item: player["inventory"][item] for item in ("3303", "30074", "30053")} == {"3303": 0, "30074": 0, "30053": 0}

    monkeypatch.setattr(char_build, "faketime", lambda: 29799)
    before = player.copy()
    with pytest.raises(HTTPException, match="specialization_not_complete"):
        await charBuild_completeUpgradeSpecialization.__wrapped__(player, complete_request())
    assert player.copy() == before

    monkeypatch.setattr(char_build, "faketime", lambda: 29800)
    await charBuild_completeUpgradeSpecialization.__wrapped__(player, complete_request())
    skill = char["tmpl"]["char_002_amiya"]["skills"][0]
    assert skill["specializeLevel"] == 1
    assert skill["completeUpgradeTime"] == -1
    assert room["trainee"]["state"] == 3


@pytest.mark.asyncio
async def test_specialization_m1_m2_m3_are_sequential_and_skill_specific(monkeypatch):
    player = make_player()
    char = prepare_training(player)
    monkeypatch.setattr(char_build, "faketime", lambda: 1000)
    skills = char["tmpl"]["char_002_amiya"]["skills"]
    costs = [
        {"3303": 5, "30084": 3, "30063": 2},
        {"3303": 6, "30104": 3, "30084": 5},
        {"3303": 10, "30115": 4, "30074": 5},
    ]
    durations = [28800, 57600, 86400]
    now = 1000
    for target, (cost, duration) in enumerate(zip(costs, durations), start=1):
        add_costs(player, cost)
        monkeypatch.setattr(char_build, "faketime", lambda now=now: now)
        await charBuild_upgradeSpecialization.__wrapped__(player, start_request(1, target))
        now += duration
        monkeypatch.setattr(char_build, "faketime", lambda now=now: now)
        await charBuild_completeUpgradeSpecialization.__wrapped__(player, complete_request())
        skills = char["tmpl"]["char_002_amiya"]["skills"]
        assert skills[1]["specializeLevel"] == target
        assert skills[0]["specializeLevel"] == 0

    before = player.copy()
    with pytest.raises(HTTPException, match="specialization_at_level_cap"):
        await charBuild_upgradeSpecialization.__wrapped__(player, start_request(1, 4))
    assert player.copy() == before


@pytest.mark.asyncio
async def test_specialization_rejects_room_level_conditions_and_material_shortage_atomically():
    player = make_player()
    char = prepare_training(player, level=1)
    char["evolvePhase"] = 1
    before = player.copy()
    with pytest.raises(HTTPException, match="specialization_condition_not_met"):
        await charBuild_upgradeSpecialization.__wrapped__(player, start_request())
    assert player.copy() == before

    char["evolvePhase"] = 2
    skills = char["tmpl"]["char_002_amiya"]["skills"].copy()
    row = skills[0].copy()
    row["specializeLevel"] = 1
    skills[0] = row
    char["tmpl"]["char_002_amiya"]["skills"] = skills
    before = player.copy()
    with pytest.raises(HTTPException, match="training_room_level_insufficient"):
        await charBuild_upgradeSpecialization.__wrapped__(player, start_request(0, 2))
    assert player.copy() == before

    skills = char["tmpl"]["char_002_amiya"]["skills"].copy()
    row = skills[0].copy()
    row["specializeLevel"] = 0
    skills[0] = row
    char["tmpl"]["char_002_amiya"]["skills"] = skills
    before = player.copy()
    with pytest.raises(HTTPException, match="insufficient_material"):
        await charBuild_upgradeSpecialization.__wrapped__(player, start_request())
    assert player.copy() == before


@pytest.mark.asyncio
async def test_specialization_start_complete_replay_and_reload_are_idempotent(monkeypatch):
    delta, extra = DeltaJson(), {}
    player = make_player(delta, extra)
    char = prepare_training(player)
    add_costs(player, {"3303": 5, "30074": 3, "30053": 5})
    monkeypatch.setattr(char_build, "faketime", lambda: 1000)
    await charBuild_upgradeSpecialization.__wrapped__(player, start_request())
    after_start = player.copy()
    await charBuild_upgradeSpecialization.__wrapped__(player, start_request())
    assert player.copy() == after_start

    monkeypatch.setattr(char_build, "faketime", lambda: 29800)
    await charBuild_completeUpgradeSpecialization.__wrapped__(player, complete_request())
    after_complete = player.copy()
    await charBuild_completeUpgradeSpecialization.__wrapped__(player, complete_request())
    assert player.copy() == after_complete
    reloaded = make_player(delta, extra)
    skill = reloaded["troop"]["chars"]["2"]["tmpl"]["char_002_amiya"]["skills"][0]
    assert skill["specializeLevel"] == 1
    assert skill["completeUpgradeTime"] == -1
