from pathlib import Path

import pytest

from openbachelors.util import player_data as player_data_module
from openbachelors.util.battle_settlement import (
    CLIENT_BATTLE_ID,
    _stage_record,
    activate_battle_start,
    commit_battle_finish,
    prepare_battle_finish,
    prepare_battle_start,
)
from openbachelors.util.campaign_lifecycle import (
    current_campaign_open_state,
    reconcile_campaign_unlocks,
)
from openbachelors.util.player_data import (
    DeltaJson,
    OverlayJson,
    PlayerData,
    post_tutorial_player_data_template,
)


class Extra:
    def __init__(self):
        self.save_obj = {}


def player():
    value = OverlayJson(post_tutorial_player_data_template, DeltaJson())
    value.extra_save = Extra()
    value.preset = "post_tutorial"
    return value


def test_campaign_remains_locked_before_one_five_pass():
    value = player()
    value["dungeon"]["stages"]["main_01-05"] = _stage_record("main_01-05")

    assert reconcile_campaign_unlocks(value) == []
    assert "camp_01" not in value["campaignsV2"]["open"]["permanent"].copy()
    assert "camp_01" not in value["campaignsV2"]["instances"]


def test_one_five_pass_opens_fresh_campaign_instance_idempotently():
    value = player()
    value["dungeon"]["stages"]["main_01-05"] = _stage_record("main_01-05", 2)

    changes = reconcile_campaign_unlocks(value)

    assert {"permanent_opened", "instance_initialized"} <= {
        row["kind"] for row in changes
    }
    assert value["campaignsV2"]["open"]["permanent"].copy() == ["camp_01"]
    assert value["campaignsV2"]["instances"]["camp_01"].copy() == {
        "maxKills": 0,
        "rewardStatus": [0] * 8,
    }
    expected = current_campaign_open_state()
    assert value["campaignsV2"]["activeGroupId"] == expected["activeGroupId"]
    assert value["campaignsV2"]["open"]["rotateGroup"] == expected["rotateGroup"]
    assert value["campaignsV2"]["open"]["trainingGroup"] == expected["trainingGroup"]
    assert (
        value["campaignsV2"]["open"]["trainingAllOpenGroup"]
        == expected["trainingAllOpenGroup"]
    )
    assert value["dungeon"]["stages"]["main_01-05"]["state"] == 2
    assert reconcile_campaign_unlocks(value) == []


def test_one_five_settlement_journals_campaign_unlock():
    value = player()
    value["dungeon"]["stages"]["main_01-04"] = _stage_record("main_01-04", 2)
    value["dungeon"]["stages"]["main_01-05"] = _stage_record("main_01-05")
    session, reason = prepare_battle_start(
        value,
        {
            "stageId": "main_01-05",
            "startTs": 100,
            "battleType": 0,
            "usePracticeTicket": 0,
        },
    )
    assert reason == "new_session"
    activate_battle_start(value, session)
    settlement = prepare_battle_finish(
        value,
        {
            "battleId": CLIENT_BATTLE_ID,
            "stageId": "main_01-05",
            "interrupt": 0,
            "giveUp": 0,
            "completeState": 2,
            "percent": 100,
            "killCnt": 1,
            "platform": 2,
            "battleData": {"beginTs": 100, "endTs": 110},
        },
        "cipher",
    )

    assert settlement["kind"] == "prepared"
    assert {row["kind"] for row in settlement["session"]["rewards"]["campaignChanges"]} >= {
        "permanent_opened",
        "instance_initialized",
        "active_group_synchronized",
        "open_field_synchronized",
    }
    commit_battle_finish(value, settlement["session"])
    assert value["campaignsV2"]["instances"]["camp_01"]["maxKills"] == 0
    assert "camp_01" in settlement["response"]["unlockStages"]


@pytest.mark.asyncio
async def test_old_save_is_repaired_on_login_and_survives_reload(tmp_path, monkeypatch):
    root = Path(tmp_path) / "multi_sav"
    monkeypatch.setattr(player_data_module, "MULTI_USER_SAV_DIRPATH", str(root))
    initial = await PlayerData.create(player_id="campaign-old", preset="post_tutorial")
    initial["dungeon"]["stages"]["main_01-05"] = _stage_record("main_01-05", 2)
    await initial.save()

    repaired = await PlayerData.create(player_id="campaign-old", preset="post_tutorial")
    assert repaired["campaignsV2"]["open"]["permanent"].copy() == ["camp_01"]
    assert repaired["campaignsV2"]["instances"]["camp_01"].copy() == {
        "maxKills": 0,
        "rewardStatus": [0] * 8,
    }
    await repaired.save()

    reloaded = await PlayerData.create(player_id="campaign-old", preset="post_tutorial")
    assert reconcile_campaign_unlocks(reloaded) == []
    assert reloaded["campaignsV2"]["instances"]["camp_01"]["maxKills"] == 0
