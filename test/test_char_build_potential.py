import pytest
from fastapi import HTTPException

from openbachelors.bp.bp_charBuild import charBuild_boostPotential, router
from openbachelors.util.player_data import DeltaJson

from .test_char_build_evolve_skill import (
    add_normalised_char,
    make_player,
    make_request,
)


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
