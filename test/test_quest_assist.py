import json

import pytest
from starlette.requests import Request

from openbachelors.bp.bp_quest import quest_getAssistList
from openbachelors.util.player_data import (
    DeltaJson,
    OverlayJson,
    post_tutorial_player_data_template,
)


class FakeExtra:
    def __init__(self):
        self.save_obj = {}


def request(payload):
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
            "path": "/quest/getAssistList",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )


@pytest.mark.asyncio
async def test_empty_post_tutorial_assist_profession_returns_empty_list_not_500():
    delta = DeltaJson()
    player = OverlayJson(post_tutorial_player_data_template, delta)
    player.extra_save = FakeExtra()

    response = await quest_getAssistList.__wrapped__(
        player,
        request({"profession": "PIONEER", "askRefresh": 0, "currSquadId": "0"}),
    )

    assert response == {"allowAskTs": 1700000000, "assistList": []}
    assert delta.modified_dict == {}
    assert player.extra_save.save_obj == {}


@pytest.mark.asyncio
async def test_refreshing_empty_assist_list_is_also_side_effect_free():
    delta = DeltaJson()
    player = OverlayJson(post_tutorial_player_data_template, delta)
    player.extra_save = FakeExtra()

    response = await quest_getAssistList.__wrapped__(
        player,
        request({"profession": "PIONEER", "askRefresh": 1, "currSquadId": "0"}),
    )

    assert response["assistList"] == []
    assert delta.modified_dict == {}
    assert player.extra_save.save_obj == {}
