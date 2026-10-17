import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from openbachelors.bp.bp_charBuild import (
    calculate_char_upgrade,
    charBuild_upgradeChar,
    router as char_build_router,
)
from openbachelors.util.player_data import (
    DeltaJson,
    OverlayJson,
    post_tutorial_player_data_template,
)


class FakeExtra:
    def __init__(self, save_obj=None):
        self.save_obj = save_obj if save_obj is not None else {}


def make_player(delta=None, extra=None):
    player = OverlayJson(post_tutorial_player_data_template, delta or DeltaJson())
    player.extra_save = FakeExtra(extra)
    return player


def make_request(payload):
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
            "path": "/charBuild/upgradeChar",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )


def payload(char_inst_id=2, item_id="2001", count=1):
    return {
        "charInstId": char_inst_id,
        "expMats": [{"id": item_id, "count": count}],
    }


def test_upgrade_char_route_is_registered():
    assert any(
        route.path == "/charBuild/upgradeChar" and "POST" in route.methods
        for route in char_build_router.routes
    )


@pytest.mark.asyncio
async def test_upgrade_char_uses_resource_exp_and_cost_and_persists():
    delta = DeltaJson()
    player = make_player(delta)
    player["inventory"]["2001"] = 2
    before_gold = player["status"]["gold"]

    response = await charBuild_upgradeChar.__wrapped__(player, make_request(payload()))

    assert response == {}
    assert player["troop"]["chars"]["2"]["level"] == 2
    assert player["troop"]["chars"]["2"]["exp"] == 100
    assert player["inventory"]["2001"] == 1
    assert player["status"]["gold"] == before_gold - 61

    reloaded = make_player(delta)
    assert reloaded["troop"]["chars"]["2"]["level"] == 2
    assert reloaded["troop"]["chars"]["2"]["exp"] == 100
    assert reloaded["inventory"]["2001"] == 1
    assert reloaded["status"]["gold"] == before_gold - 61


@pytest.mark.asyncio
async def test_upgrade_char_combines_duplicate_exp_material_entries():
    player = make_player()
    player["inventory"]["2001"] = 2
    req = {
        "charInstId": 2,
        "expMats": [{"id": "2001", "count": 1}, {"id": "2001", "count": 1}],
    }

    await charBuild_upgradeChar.__wrapped__(player, make_request(req))

    assert player["inventory"]["2001"] == 0
    assert player["troop"]["chars"]["2"]["level"] == 4
    assert player["troop"]["chars"]["2"]["exp"] == 49


@pytest.mark.asyncio
async def test_upgrade_char_insufficient_material_is_atomic():
    player = make_player()
    player["inventory"]["2001"] = 0
    before = player.copy()

    with pytest.raises(HTTPException) as exc:
        await charBuild_upgradeChar.__wrapped__(player, make_request(payload()))

    assert exc.value.detail == "insufficient_exp_material"
    assert player.copy() == before


@pytest.mark.asyncio
async def test_upgrade_char_insufficient_gold_is_atomic():
    player = make_player()
    player["inventory"]["2001"] = 1
    player["status"]["gold"] = 0
    before = player.copy()

    with pytest.raises(HTTPException) as exc:
        await charBuild_upgradeChar.__wrapped__(player, make_request(payload()))

    assert exc.value.detail == "insufficient_gold"
    assert player.copy() == before


def test_upgrade_char_rejects_phase_level_cap():
    char = {
        "charId": "char_002_amiya",
        "evolvePhase": 0,
        "level": 50,
        "exp": 0,
    }
    with pytest.raises(ValueError, match="character_at_level_cap"):
        calculate_char_upgrade(char, 200)


@pytest.mark.asyncio
async def test_upgrade_char_advances_existing_guide_1_only_once_per_upgrade():
    player = make_player()
    player["inventory"]["2001"] = 2
    player["mission"]["missions"]["GUIDE"]["guide_1"] = {
        "progress": [{"target": 1, "value": 0}],
        "state": 0,
    }

    await charBuild_upgradeChar.__wrapped__(player, make_request(payload()))

    mission = player["mission"]["missions"]["GUIDE"]["guide_1"]
    assert mission["progress"][0]["value"] == 1
    assert mission["state"] == 2

    await charBuild_upgradeChar.__wrapped__(player, make_request(payload()))
    assert mission["progress"][0]["value"] == 1
    assert mission["state"] == 2


@pytest.mark.asyncio
async def test_identical_replay_is_ignored_and_journal_survives_reload():
    delta = DeltaJson()
    extra = {}
    player = make_player(delta, extra)
    player["inventory"]["2001"] = 1

    await charBuild_upgradeChar.__wrapped__(player, make_request(payload()))
    after = player.copy()

    reloaded = make_player(delta, extra)
    await charBuild_upgradeChar.__wrapped__(reloaded, make_request(payload()))

    assert reloaded.copy() == after
