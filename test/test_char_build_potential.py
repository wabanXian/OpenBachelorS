import json
import time

import pytest
from fastapi import HTTPException

from openbachelors.bp.bp_charBuild import charBuild_boostPotential, router
import openbachelors.util.player_data as player_data_module
from openbachelors.util.player_data import (
    DeltaJson,
    OverlayJson,
    PlayerData,
    post_tutorial_player_data_template,
)

from .test_char_build_evolve_skill import (
    FakeExtra,
    add_normalised_char,
    make_player,
    make_request,
)


def decorated_player():
    class SavableDelta(DeltaJson):
        async def save(self):
            return None

    saved, pending = SavableDelta(), SavableDelta()
    base = OverlayJson(post_tutorial_player_data_template, saved)
    player = PlayerData(base, pending)
    player.save_aggregator = None
    player.sav_delta_json = saved
    player.sav_pending_delta_json = pending
    player.extra_save = FakeExtra({"last_message_ts": time.time()})
    player.json_with_delta = base
    player.preset = "post_tutorial"
    return player


def potential_request(char_inst_id, item_id, target_rank):
    return make_request(
        "/charBuild/boostPotential",
        {"charInstId": char_inst_id, "itemId": item_id, "targetRank": target_rank},
    )


def test_potential_route_is_registered():
    assert "/charBuild/boostPotential" in {
        route.path for route in router.routes if "POST" in route.methods
    }


@pytest.mark.asyncio
async def test_exclusive_token_boosts_one_rank_and_persists():
    delta, extra = DeltaJson(), {}
    player = make_player(delta, extra)
    char = add_normalised_char(player, "char_208_melan")
    player["inventory"]["p_char_208_melan"] = 2

    response = await charBuild_boostPotential.__wrapped__(
        player, potential_request(999, "p_char_208_melan", 1)
    )

    assert response == {"resultOneSuc": 1}
    assert char["potentialRank"] == 1
    assert player["inventory"]["p_char_208_melan"] == 1
    reloaded = make_player(delta, extra)
    assert reloaded["troop"]["chars"]["999"]["potentialRank"] == 1
    assert reloaded["inventory"]["p_char_208_melan"] == 1


@pytest.mark.asyncio
async def test_http_response_uses_pc_success_value_and_contains_complete_delta(monkeypatch):
    player = decorated_player()
    char = add_normalised_char(player, "char_208_melan")
    player["inventory"]["p_char_208_melan"] = 1

    async def create_player(cls, *args, **kwargs):
        return player

    monkeypatch.setattr(player_data_module.PlayerData, "create", classmethod(create_player))
    response = await charBuild_boostPotential(
        request=potential_request(999, "p_char_208_melan", 1)
    )
    payload = json.loads(response.body)

    # PC 2.7.81 callback compares resultOneSuc to exactly 1 before showing
    # CharacterInfoPotentialNotifyView's success panel.
    assert payload["resultOneSuc"] == 1
    modified = payload["playerDataDelta"]["modified"]
    assert modified["troop"]["chars"]["999"]["potentialRank"] == 1
    assert modified["inventory"]["p_char_208_melan"] == 0
    assert char["potentialRank"] == 1


@pytest.mark.asyncio
async def test_general_tokens_require_four_and_wrong_profession_is_rejected_atomically():
    player = make_player()
    char = add_normalised_char(player, "char_208_melan")
    player["inventory"]["tier3_guard"] = 4
    player["inventory"]["tier3_sniper"] = 4

    before = player.copy()
    with pytest.raises(HTTPException, match="invalid_potential_item"):
        await charBuild_boostPotential.__wrapped__(
            player, potential_request(999, "tier3_sniper", 1)
        )
    assert player.copy() == before

    await charBuild_boostPotential.__wrapped__(
        player, potential_request(999, "tier3_guard", 1)
    )
    assert char["potentialRank"] == 1
    assert player["inventory"]["tier3_guard"] == 0


@pytest.mark.asyncio
async def test_potential_rejects_shortage_skip_and_cap_without_mutation():
    player = make_player()
    char = add_normalised_char(player, "char_208_melan")
    player["inventory"]["p_char_208_melan"] = 0
    for target, error in ((1, "insufficient_potential_item"), (2, "invalid_potential_rank_transition")):
        before = player.copy()
        with pytest.raises(HTTPException, match=error):
            await charBuild_boostPotential.__wrapped__(
                player, potential_request(999, "p_char_208_melan", target)
            )
        assert player.copy() == before

    char["potentialRank"] = 5
    player["inventory"]["p_char_208_melan"] = 1
    before = player.copy()
    with pytest.raises(HTTPException, match="potential_at_rank_cap"):
        await charBuild_boostPotential.__wrapped__(
            player, potential_request(999, "p_char_208_melan", 6)
        )
    assert player.copy() == before


@pytest.mark.asyncio
async def test_potential_replay_does_not_consume_twice_and_preserves_template_data():
    extra = {}
    player = make_player(extra=extra)
    char = player["troop"]["chars"]["2"]
    player["inventory"]["p_char_002_amiya"] = 2
    template_before = char["tmpl"].copy()
    request = potential_request(2, "p_char_002_amiya", 1)

    await charBuild_boostPotential.__wrapped__(player, request)
    after = player.copy()
    await charBuild_boostPotential.__wrapped__(
        player, potential_request(2, "p_char_002_amiya", 1)
    )

    assert player.copy() == after
    assert char["potentialRank"] == 1
    assert char["tmpl"].copy() == template_before
    assert player["inventory"]["p_char_002_amiya"] == 1
