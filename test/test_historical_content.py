from copy import deepcopy
import asyncio

from openbachelors.bp.bp_gacha import (
    _cache_gacha_response,
    _cached_gacha_response,
    _consume_advanced_gacha_cost,
    _historical_pool_error,
    get_advanced_gacha_manager,
)
from openbachelors.bp.bp_account import account_historicalContentDate
import openbachelors.tool.historical_content_save as date_tool
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


def player_at(date=None):
    value = Player(post_tutorial_player_data_template, DeltaJson())
    extra = ExtraSave()
    extra.save_obj = ExtraSave.get_default_save_obj()
    if date:
        extra.save_obj[CONTENT_DATE_KEY] = {"date": date, "mode": "historical"}
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
    player.extra_save.save_obj[CONTENT_DATE_KEY] = {"date": "2025-01-01", "mode": "historical"}
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


def test_authenticated_content_date_response_uses_only_content_clock():
    player = player_at("2019-11-20")
    player.username = "post_tutorial::isolated"
    endpoint = account_historicalContentDate.__wrapped__
    response = asyncio.run(endpoint(player, None))
    assert response == {
        "result": 0,
        "mode": "historical",
        "date": "2019-11-20",
        "contentTs": 1574222400,
        "account": "post_tutorial::isolated",
    }


def test_set_show_reset_date_is_persistent_and_backed_up(monkeypatch):
    storage = {"kept": 1}
    monkeypatch.setattr(date_tool, "_load", lambda path: deepcopy(storage))
    monkeypatch.setattr(date_tool, "_backup", lambda save_dir: date_tool.Path("backup.zip"))
    monkeypatch.setattr(date_tool, "_write", lambda path, value: (storage.clear(), storage.update(value)))
    save_dir = date_tool.Path("isolated")
    backup, report = set_date(save_dir, "2019-11-20", yes=True)
    assert str(backup) == "backup.zip"
    assert report["mode"] == "historical"
    assert show_date(save_dir)["date"] == "2019-11-20"
    backup2, report2 = reset_date(save_dir, yes=True)
    assert str(backup2) == "backup.zip"
    assert report2["mode"] == "realtime"
    assert report2["date"] is None
    assert storage["kept"] == 1
