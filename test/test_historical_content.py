from copy import deepcopy
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from openbachelors.bp.bp_gacha import (
    _cache_gacha_response,
    _cached_gacha_response,
    _consume_advanced_gacha_cost,
    _historical_pool_error,
    get_advanced_gacha_manager,
)
from openbachelors.bp.bp_account import account_historicalContentDate
import openbachelors.tool.historical_content_save as date_tool
import openbachelors.util.historical_release as historical_release
from openbachelors.tool.historical_content_save import reset_date, set_date, show_date
from openbachelors.util.extra_save import ExtraSave
from openbachelors.util.battle_settlement import (
    CLIENT_BATTLE_ID,
    activate_battle_start,
    commit_battle_finish,
    prepare_battle_finish,
    prepare_battle_start,
)
from openbachelors.util.historical_release import (
    CONTENT_DATE_KEY,
    active_activity_ids,
    active_pool_ids,
    complete_historical_story_stage,
    content_date,
    content_date_status,
    historical_pool_up_info,
    historical_pool_roster,
    pool_allowed,
    reconcile_historical_release,
    stage_allowed,
    unlock_retro_block,
)
from openbachelors.util.player_data import DeltaJson, OverlayJson, post_tutorial_player_data_template
from openbachelors.util.settlement_rules import get_stage_rule


class Player(OverlayJson):
    pass


@pytest.fixture(autouse=True)
def isolated_global_content_date(tmp_path, monkeypatch):
    path = tmp_path / "sav" / "historical_content.json"
    monkeypatch.setattr(historical_release, "HISTORICAL_CONTENT_CONFIG", str(path))
    yield path


def write_global_date(value):
    path = date_tool.Path(historical_release.HISTORICAL_CONTENT_CONFIG)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 1, "mode": "historical", "date": value}))


def player_at(date=None):
    value = Player(post_tutorial_player_data_template, DeltaJson())
    extra = ExtraSave()
    extra.save_obj = ExtraSave.get_default_save_obj()
    if date:
        write_global_date(date)
    value.extra_save = extra
    value["dungeon"]["stages"]["main_01-10"] = {
        "stageId": "main_01-10", "completeTimes": 1, "startTimes": 1,
        "practiceTimes": 0, "state": 3, "hasBattleReplay": 0, "noCostCnt": 0,
    }
    return value


def test_date_independently_selects_activity_and_all_naturally_open_pools():
    player = player_at("2019-11-20")
    assert content_date(player).isoformat() == "2019-11-20"
    assert "act5d0" in active_activity_ids(player)
    assert {"NORM_4_0_5", "NORM_5_0_1"} <= active_pool_ids(player)
    changes = reconcile_historical_release(player)
    assert stage_allowed(player, "act5d0_01")
    assert pool_allowed(player, "NORM_5_0_1")
    assert "act5d0_01" in player["dungeon"]["stages"]
    assert "NORM_4_0_5" in player["gacha"]["normal"]
    assert "NORM_5_0_1" in player["gacha"]["normal"]
    assert _historical_pool_error(player, "NORM_4_0_5") is None
    assert changes


def test_permanent_retro_is_not_controlled_by_historical_date():
    player = player_at("2025-01-01")
    assert "act5d0" not in active_activity_ids(player)
    assert not stage_allowed(player, "act5d0_01")
    before_coin = player["retro"]["coin"]
    error, changes = unlock_retro_block(player, "permanent_sidestory_3_Code_Of_Brawl")
    assert error is None
    assert player["retro"]["coin"] == before_coin - 1
    assert "retro.block.permanent_sidestory_3_Code_Of_Brawl" in changes
    assert stage_allowed(player, "act5d0_01")
    assert "act5d0_01" in player["dungeon"]["stages"]
    assert unlock_retro_block(player, "permanent_sidestory_3_Code_Of_Brawl") == (None, [])


def test_date_switch_preserves_event_progress_and_pool_pity():
    player = player_at("2019-11-20")
    reconcile_historical_release(player)
    player["dungeon"]["stages"]["act5d0_01"]["state"] = 3
    player["gacha"]["normal"]["NORM_5_0_1"]["cnt"] = 7
    write_global_date("2025-01-01")
    reconcile_historical_release(player)
    assert player["dungeon"]["stages"]["act5d0_01"]["state"] == 3
    assert player["gacha"]["normal"]["NORM_5_0_1"]["cnt"] == 7
    assert not pool_allowed(player, "NORM_5_0_1")


def test_verified_event_rules_and_story_chain():
    player = player_at("2019-11-20")
    reconcile_historical_release(player)
    rule, source = get_stage_rule("act5d0_05")
    assert source == "verified_historical_release_rule"
    assert rule["firstPassRewards"] == [{"type": "CHAR", "id": "char_325_bison", "count": 1}]
    player["dungeon"]["stages"]["act5d0_st01"] = {
        "stageId": "act5d0_st01", "completeTimes": 0, "startTimes": 0,
        "practiceTimes": 0, "state": 0, "hasBattleReplay": 0, "noCostCnt": 0,
    }
    result = complete_historical_story_stage(player, "activities/act5d0/level_act5d0_st01")
    assert result["changed"] is True
    assert "act5d0_06" in player["dungeon"]["stages"]


def test_code_of_brawl_battle_uses_persistent_session_and_unlocks_successor():
    player = player_at("2019-11-20")
    reconcile_historical_release(player)
    session, reason = prepare_battle_start(
        player,
        {"stageId": "act5d0_01", "startTs": 100, "battleType": 0,
         "usePracticeTicket": 0},
    )
    assert reason == "new_session"
    activate_battle_start(player, session)
    decoded = {
        "battleId": CLIENT_BATTLE_ID, "stageId": "act5d0_01",
        "interrupt": 0, "giveUp": 0, "completeState": 3,
        "percent": 100, "killCnt": 30, "platform": 2,
        "battleData": {"beginTs": 100, "endTs": 200},
    }
    prepared = prepare_battle_finish(player, decoded, "event-cipher")
    assert prepared["kind"] == "prepared"
    commit_battle_finish(player, prepared["session"])
    assert player["dungeon"]["stages"]["act5d0_01"]["state"] == 3
    assert "act5d0_02" in player["dungeon"]["stages"]
    duplicate = prepare_battle_finish(player, deepcopy(decoded), "event-retry")
    assert duplicate["kind"] == "duplicate"


def test_verified_banner_draw_and_pity_persist():
    up = historical_pool_up_info("NORM_5_0_1")
    assert up["TIER_6"] == {"char_id_lst": ["char_213_mostma"], "percent": 0.5}
    player = player_at("2019-11-20")
    reconcile_historical_release(player)
    player["status"]["diamondShard"] = 600
    request = {"poolId": "NORM_5_0_1", "useTkt": 0, "itemId": "4003"}
    assert _consume_advanced_gacha_cost(player, request, 1) is None
    response = {}
    get_advanced_gacha_manager(player, request, response).gacha_advancedGacha()
    assert response["result"] == 0
    assert player["gacha"]["normal"]["NORM_5_0_1"]["cnt"] == 1


def test_all_visible_2019_11_20_pools_have_audited_rules_and_period_rosters():
    player = player_at("2019-11-20")
    visible = active_pool_ids(player)
    assert {"NORM_4_0_5", "NORM_5_0_1"} <= visible
    for pool_id in visible:
        assert _historical_pool_error(player, pool_id) is None
        if pool_id.startswith("NORM_"):
            assert historical_pool_up_info(pool_id)

    from openbachelors.util.const_json_loader import const_json_loader
    from openbachelors.const.filepath import GACHA_DATA
    classic = const_json_loader[GACHA_DATA]["classic_avail_char_info"]
    older = historical_pool_roster("NORM_4_0_5", classic)
    assert older["TIER_6"]["char_id_lst"][-1] == "char_248_mgllan"
    assert older["TIER_5"]["char_id_lst"][-1] == "char_279_excu"
    assert older["TIER_4"]["char_id_lst"][-1] == "char_190_clour"
    assert "char_213_mostma" not in older["TIER_6"]["char_id_lst"]


def test_2019_standard_rotation_up_weights_and_shared_six_star_pity():
    player = player_at("2019-11-20")
    reconcile_historical_release(player)
    first = get_advanced_gacha_manager(
        player, {"poolId": "NORM_4_0_5"}, {},
    )
    second = get_advanced_gacha_manager(
        player, {"poolId": "NORM_5_0_1"}, {},
    )
    assert first.get_up_char_info()["TIER_6"]["percent"] == 0.25
    assert sum(
        first.get_up_char_info()["TIER_5"]["percent"]
        for _ in first.get_up_char_info()["TIER_5"]["char_id_lst"]
    ) == 0.5
    first.set_basic_tier_6_pity(37)
    assert second.get_basic_tier_6_pity() == 37
    assert first.get_basic_tier_5_pity_key() != second.get_basic_tier_5_pity_key()


def test_first_ten_guarantee_is_per_pool_and_replay_cache_is_immutable():
    player = player_at("2019-11-20")
    reconcile_historical_release(player)
    player.extra_save.save_obj["advanced_gacha_num_NORM_4_0_5"] = 9
    manager = get_advanced_gacha_manager(
        player, {"poolId": "NORM_4_0_5"}, {},
    )
    char_id = manager.get_advanced_gacha_result()
    assert char_id in manager.get_avail_char_info()["TIER_5"]["char_id_lst"].copy()
    assert manager.get_basic_tier_5_pity() is True
    other = get_advanced_gacha_manager(
        player, {"poolId": "NORM_5_0_1"}, {},
    )
    assert other.get_basic_tier_5_pity() is False

    response = {"result": 0, "charGet": {"charId": char_id}}
    _cache_gacha_response(player, "same-request", response)
    replay = _cached_gacha_response(player, "same-request")
    replay["charGet"]["charId"] = "changed-by-caller"
    assert _cached_gacha_response(player, "same-request") == response


def test_loopback_content_date_response_uses_only_server_content_clock():
    player = player_at("2019-11-20")
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    response = asyncio.run(account_historicalContentDate(request))
    assert response == {
        "result": 0,
        "mode": "historical",
        "date": "2019-11-20",
        "contentTs": 1574222400,
    }


def test_content_date_endpoint_rejects_non_loopback_and_reports_realtime(
    isolated_global_content_date,
):
    remote = SimpleNamespace(client=SimpleNamespace(host="192.168.1.10"))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(account_historicalContentDate(remote))
    assert exc.value.status_code == 403

    isolated_global_content_date.parent.mkdir(parents=True, exist_ok=True)
    isolated_global_content_date.write_text(
        json.dumps({"schema": 1, "mode": "realtime", "date": None}),
        encoding="utf-8",
    )
    local = SimpleNamespace(client=SimpleNamespace(host="::1"))
    assert asyncio.run(account_historicalContentDate(local)) == {
        "result": 0,
        "mode": "realtime",
        "date": None,
        "contentTs": None,
    }


def test_set_show_reset_date_is_server_global_and_persistent(tmp_path, monkeypatch):
    config_path = tmp_path / "sav" / "historical_content.json"
    monkeypatch.setattr(
        date_tool,
        "_backup",
        lambda path: tmp_path / "backup.json" if path.is_file() else None,
    )
    backup, report = set_date(config_path, "2019-11-20", yes=True, legacy_root=tmp_path)
    assert backup is None
    assert report["mode"] == "historical"
    assert show_date(config_path, legacy_root=tmp_path)["date"] == "2019-11-20"
    backup2, report2 = reset_date(config_path, yes=True, legacy_root=tmp_path)
    assert backup2 == tmp_path / "backup.json"
    assert report2["mode"] == "realtime"
    assert report2["date"] is None


def test_legacy_account_dates_are_read_only_and_conflicts_are_reported(tmp_path):
    for account, value in (("a", "2019-11-20"), ("b", "2020-01-01")):
        path = tmp_path / "multi_sav" / "presets" / "post_tutorial" / account / "extra.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({CONTENT_DATE_KEY: {"date": value}}), encoding="utf-8")
    config = tmp_path / "sav" / "historical_content.json"
    status = content_date_status(config, tmp_path)
    assert status["date"] is None
    assert status["conflicts"] == ["2019-11-20", "2020-01-01"]
    assert all(path.is_file() for path in tmp_path.rglob("extra.json"))

    set_date(config, "2019-11-20", yes=True, legacy_root=tmp_path)
    status = content_date_status(config, tmp_path)
    assert status["date"].isoformat() == "2019-11-20"
    assert len(status["legacy"]) == 2
