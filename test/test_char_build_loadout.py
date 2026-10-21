import pytest
from fastapi import HTTPException

from openbachelors.bp.bp_charBuild import (
    charBuild_changeCharSkin,
    charBuild_changeCharTemplate,
    charBuild_setDefaultSkill,
)
from openbachelors.bp.legacy_bp import user_changeSecretary
from openbachelors.util.character_loadout import plan_template_unlocks, reconcile_template_unlocks
from openbachelors.util.favor import build_squad_favor_snapshot
from openbachelors.util.player_data import DeltaJson

from .test_char_build_evolve_skill import make_player, make_request


AMIYA_GUARD = "char_1001_amiya2"
AMIYA_MEDIC = "char_1037_amiya3"


def stage_record(stage_id, state=2):
    return {
        "stageId": stage_id,
        "completeTimes": 1,
        "startTimes": 1,
        "practiceTimes": 0,
        "state": state,
        "hasBattleReplay": 0,
        "noCostCnt": 0,
    }


@pytest.mark.asyncio
async def test_change_skin_requires_owner_template_phase_and_owned_outfit():
    player = make_player()
    char = player["troop"]["chars"]["2"]
    owned_before = player["skin"]["characterSkins"].copy()

    with pytest.raises(HTTPException, match="builtin_skin_not_unlocked"):
        await charBuild_changeCharSkin.__wrapped__(
            player,
            make_request("/charBuild/changeCharSkin", {"charInstId": 2, "skinId": "char_002_amiya#2"}),
        )
    with pytest.raises(HTTPException, match="skin_not_owned"):
        await charBuild_changeCharSkin.__wrapped__(
            player,
            make_request("/charBuild/changeCharSkin", {"charInstId": 2, "skinId": "char_002_amiya@winter#1"}),
        )
    with pytest.raises(HTTPException, match="skin_character_mismatch"):
        await charBuild_changeCharSkin.__wrapped__(
            player,
            make_request("/charBuild/changeCharSkin", {"charInstId": 2, "skinId": "char_208_melan#1"}),
        )

    player["skin"]["characterSkins"]["char_002_amiya@winter#1"] = 1
    await charBuild_changeCharSkin.__wrapped__(
        player,
        make_request("/charBuild/changeCharSkin", {"charInstId": 2, "skinId": "char_002_amiya@winter#1"}),
    )
    assert char["tmpl"]["char_002_amiya"]["skinId"] == "char_002_amiya@winter#1"
    assert set(player["skin"]["characterSkins"].copy()) == set(owned_before) | {"char_002_amiya@winter#1"}


@pytest.mark.asyncio
async def test_default_skill_requires_unlocked_index_and_keeps_template_state_isolated():
    player = make_player()
    char = player["troop"]["chars"]["2"]
    with pytest.raises(HTTPException, match="default_skill_not_unlocked"):
        await charBuild_setDefaultSkill.__wrapped__(
            player,
            make_request("/charBuild/setDefaultSkill", {"charInstId": 2, "defaultSkillIndex": 1}),
        )
    with pytest.raises(HTTPException, match="invalid_default_skill_index"):
        await charBuild_setDefaultSkill.__wrapped__(
            player,
            make_request("/charBuild/setDefaultSkill", {"charInstId": 2, "defaultSkillIndex": True}),
        )
    await charBuild_setDefaultSkill.__wrapped__(
        player,
        make_request("/charBuild/setDefaultSkill", {"charInstId": 2, "defaultSkillIndex": 0}),
    )
    assert char["tmpl"]["char_002_amiya"]["defaultSkillIndex"] == 0


@pytest.mark.asyncio
async def test_amiya_template_unlock_uses_real_stage_condition_and_preserves_loadouts():
    player = make_player()
    char = player["troop"]["chars"]["2"]
    assert set(char["tmpl"].copy()) == {"char_002_amiya"}
    before = char.copy()
    with pytest.raises(HTTPException, match="character_template_not_unlocked"):
        await charBuild_changeCharTemplate.__wrapped__(
            player,
            make_request("/charBuild/changeCharTemplate", {"charInstId": 2, "templateId": AMIYA_GUARD}),
        )
    assert char.copy() == before

    player["dungeon"]["stages"]["main_08-16"] = stage_record("main_08-16")
    changes = reconcile_template_unlocks(player)
    assert [row["templateId"] for row in changes] == [AMIYA_GUARD]
    assert AMIYA_GUARD in char["tmpl"]
    assert AMIYA_MEDIC not in char["tmpl"]
    base = char["tmpl"]["char_002_amiya"].copy()

    await charBuild_changeCharTemplate.__wrapped__(
        player,
        make_request("/charBuild/changeCharTemplate", {"charInstId": 2, "templateId": AMIYA_GUARD}),
    )
    assert char["currentTmpl"] == AMIYA_GUARD
    assert char["tmpl"]["char_002_amiya"].copy() == base
    assert char["tmpl"][AMIYA_GUARD]["skinId"] == "char_1001_amiya2#2"


def test_template_unlock_and_per_template_state_survive_overlay_reload():
    delta = DeltaJson()
    player = make_player(delta)
    player["troop"]["chars"]["2"]["evolvePhase"] = 2
    player["troop"]["chars"]["2"]["level"] = 1
    player["dungeon"]["stages"]["main_08-16"] = stage_record("main_08-16")
    assert reconcile_template_unlocks(player)
    player["troop"]["chars"]["2"]["tmpl"][AMIYA_GUARD]["defaultSkillIndex"] = 1

    reloaded = make_player(delta)
    char = reloaded["troop"]["chars"]["2"]
    assert AMIYA_GUARD in char["tmpl"]
    assert char["tmpl"][AMIYA_GUARD]["defaultSkillIndex"] == 1
    assert char["tmpl"]["char_002_amiya"]["defaultSkillIndex"] == 0


def test_battle_plans_template_unlock_atomically_from_future_stage_state():
    player = make_player()
    char = player["troop"]["chars"]["2"]

    mutations, changes = plan_template_unlocks(
        player, {"main_08-16": 2}
    )

    assert [row["templateId"] for row in changes] == [AMIYA_GUARD]
    assert AMIYA_GUARD not in char["tmpl"]
    assert AMIYA_MEDIC not in char["tmpl"]
    paths = [mutation["path"] for mutation in mutations]
    assert ["troop", "chars", "2", "tmpl", AMIYA_GUARD] in paths
    assert not any(AMIYA_MEDIC in path for path in paths)


def test_squad_rejects_locked_skill_without_rewriting_saved_squad():
    player = make_player()
    slots = [
        {
            "charInstId": 2,
            "currentTmpl": "char_002_amiya",
            "tmpl": {"char_002_amiya": {"skillIndex": 1, "currentEquip": None}},
        }
    ]
    snapshot, error = build_squad_favor_snapshot(player, {"squad": {"slots": slots}})
    assert snapshot is None
    assert error == "squad_skill_not_unlocked"


@pytest.mark.asyncio
async def test_change_secretary_updates_only_secretary_fields():
    player = make_player()
    status_before = player["status"].copy()
    await user_changeSecretary.__wrapped__(
        player,
        make_request(
            "/user/changeSecretary",
            {"charInstId": 2, "skinId": "char_002_amiya#1"},
        ),
    )
    assert player["status"]["secretary"] == "char_002_amiya"
    assert player["status"]["secretarySkinId"] == "char_002_amiya#1"
    status_after = player["status"].copy()
    for field in {"secretary", "secretarySkinId"}:
        status_before.pop(field, None)
        status_after.pop(field, None)
    assert status_after == status_before
