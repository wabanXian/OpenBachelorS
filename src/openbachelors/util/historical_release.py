"""Historical content availability without changing the game's global clock."""

from datetime import datetime, timezone, timedelta
import json
import logging
from pathlib import Path

from ..const.filepath import (
    ACTIVITY_TABLE,
    GACHA_TABLE,
    HISTORICAL_CONTENT_CONFIG,
    HISTORICAL_RELEASES,
    RETRO_TABLE,
    STAGE_TABLE,
)
from .const_json_loader import const_json_loader
from .faketime import faketime

CONTENT_DATE_KEY = "historical_content_date_v1"
SELECTION_KEY = CONTENT_DATE_KEY  # compatibility import; list selection is retired
CN_TZ = timezone(timedelta(hours=8))
logger = logging.getLogger(__name__)
_reported_legacy_conflict = None


def _parse_date(value):
    if not isinstance(value, dict) or not isinstance(value.get("date"), str):
        return None
    try:
        return datetime.strptime(value["date"], "%Y-%m-%d").date()
    except ValueError:
        return None


def legacy_content_dates(root=Path(".")):
    """Read old per-account selections without mutating any save."""
    root = Path(root)
    paths = [root / "sav" / "extra.json"]
    multi = root / "multi_sav"
    if multi.is_dir():
        paths.extend(multi.rglob("extra.json"))
    records = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8")).get(CONTENT_DATE_KEY)
        except (OSError, ValueError):
            continue
        selected = _parse_date(value)
        if selected is not None:
            records.append({"path": str(path), "date": selected.isoformat()})
    return records


def content_date_status(config_path=None, legacy_root=Path("."), *, include_legacy=True):
    """Resolve the one server-local content date and report legacy conflicts."""
    path = Path(config_path or HISTORICAL_CONTENT_CONFIG)
    legacy = legacy_content_dates(legacy_root) if include_legacy or not path.is_file() else []
    legacy_dates = sorted({item["date"] for item in legacy})
    conflicts = legacy_dates if len(legacy_dates) > 1 else []
    if path.is_file():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            value = {}
        selected = _parse_date(value)
        return {
            "mode": "historical" if selected is not None else "realtime",
            "date": selected,
            "source": "server_config",
            "configPath": str(path),
            "legacy": legacy,
            "conflicts": conflicts,
        }

    # One unique old value is a safe, read-only compatibility fallback.  It is
    # never copied into or removed from account saves automatically.
    selected = datetime.strptime(legacy_dates[0], "%Y-%m-%d").date() if len(legacy_dates) == 1 else None
    return {
        "mode": "historical" if selected is not None else "realtime",
        "date": selected,
        "source": "legacy_compat" if selected is not None else "default_realtime",
        "configPath": str(path),
        "legacy": legacy,
        "conflicts": conflicts,
    }


def release_definitions():
    return const_json_loader[HISTORICAL_RELEASES]["releases"]


def content_date(player_data=None):
    global _reported_legacy_conflict
    status = content_date_status(include_legacy=False)
    conflicts = tuple(status["conflicts"])
    if conflicts and conflicts != _reported_legacy_conflict:
        logger.error(
            "conflicting legacy historical dates %s; using realtime until set-date",
            list(conflicts),
        )
        _reported_legacy_conflict = conflicts
    return status["date"]


def content_timestamp(player_data=None):
    """Return the content-only clock; all economy timers keep using faketime()."""
    selected = content_date(player_data)
    if selected is None:
        return int(faketime())
    midnight = datetime.combine(selected, datetime.min.time(), CN_TZ)
    return int(midnight.timestamp()) + 12 * 3600


def _in_window(timestamp, start, end):
    return int(start) <= timestamp and (int(end) < 0 or timestamp <= int(end))


def active_activity_ids(player_data):
    timestamp = content_timestamp(player_data)
    return {
        activity_id
        for activity_id, info in const_json_loader[ACTIVITY_TABLE]["basicInfo"]
        if _in_window(timestamp, info["startTime"], info["endTime"])
    }


def active_pool_ids(player_data):
    timestamp = content_timestamp(player_data)
    return {
        pool["poolId"]
        # ``carousel`` is the client display schedule.  gachaPoolClient stores
        # pool definitions and older archived entries may be null there.
        for _, pool in const_json_loader[GACHA_TABLE]["carousel"]
        if "startTime" in pool and "endTime" in pool
        if _in_window(timestamp, pool["startTime"], pool["endTime"])
    }


def permanent_retro_ids():
    now = int(faketime())
    return {
        retro_id
        for retro_id, info in const_json_loader[RETRO_TABLE]["retroActList"]
        if int(info["startTime"]) <= now
    }


def enabled_content(player_data):
    return {
        "date": content_date(player_data),
        "activities": active_activity_ids(player_data),
        "pools": active_pool_ids(player_data),
        "permanentRetros": permanent_retro_ids(),
    }


def enabled_releases(player_data):
    active = active_activity_ids(player_data)
    return [release.copy() for _, release in release_definitions() if release["activityId"] in active]


def _stage_record(stage_id):
    return {"stageId": stage_id, "completeTimes": 0, "startTimes": 0,
            "practiceTimes": 0, "state": 0, "hasBattleReplay": 0, "noCostCnt": 0}


def release_stage_ids(release):
    prefix = release["stagePrefix"]
    return {stage_id for stage_id, _ in const_json_loader[STAGE_TABLE]["stages"]
            if stage_id.startswith(prefix) and "#f#" not in stage_id}


def retro_is_unlocked(player_data, retro_id):
    blocks = player_data["retro"]["block"]
    return retro_id in blocks and blocks[retro_id]["open"] == 1


def stage_allowed(player_data, stage_id):
    active = active_activity_ids(player_data)
    for _, release in release_definitions():
        if stage_id in release_stage_ids(release):
            return release["activityId"] in active or retro_is_unlocked(player_data, release["retroId"])
    return False


def pool_allowed(player_data, pool_id):
    return pool_id in active_pool_ids(player_data)


HISTORICAL_POOL_RULES = {
    # CN standard rotation, 2019-11-14 04:00 through 2019-11-28 03:59.
    # The listed shares are conditional on first rolling the corresponding
    # rarity: 50% of six-stars and 50% of five-stars are rate-up operators.
    "NORM_4_0_5": {
        "up": {
            "TIER_6": {
                "char_id_lst": ["char_136_hsguma", "char_112_siege"],
                "percent": 0.25,
            },
            "TIER_5": {
                "char_id_lst": [
                    "char_101_sora",
                    "char_326_glacus",
                    "char_129_bluep",
                ],
                "percent": 1 / 6,
            },
        },
        "rosterCutoffs": {
            "TIER_6": "char_248_mgllan",
            "TIER_5": "char_279_excu",
            "TIER_4": "char_190_clour",
        },
        "evidence": [
            "gacha_table.gachaPoolClient.NORM_4_0_5",
            "https://wiki.biligame.com/arknights/用户:418054283/sandbox/寻访池一览/2019",
        ],
    },
    # Code of Brawl debut banner, 2019-11-19 16:00 through
    # 2019-12-03 03:59.
    "NORM_5_0_1": {
        "up": {
            "TIER_6": {"char_id_lst": ["char_213_mostma"], "percent": 0.5},
            "TIER_5": {
                "char_id_lst": ["char_243_waaifu", "char_158_milu"],
                "percent": 0.25,
            },
            "TIER_4": {"char_id_lst": ["char_133_mm"], "percent": 0.2},
        },
        "rosterCutoffs": {
            "TIER_6": "char_213_mostma",
            "TIER_5": "char_243_waaifu",
            "TIER_4": "char_133_mm",
        },
        "evidence": [
            "gacha_table.gachaPoolClient.NORM_5_0_1",
            "https://wiki.biligame.com/arknights/用户:418054283/sandbox/寻访池一览/2019",
        ],
    },
}


def historical_pool_up_info(pool_id):
    rule = HISTORICAL_POOL_RULES.get(pool_id)
    return rule["up"] if rule else None


def historical_pool_roster(pool_id, classic_roster):
    rule = HISTORICAL_POOL_RULES.get(pool_id)
    if rule is None:
        return None
    cutoffs = rule["rosterCutoffs"]
    result = {}
    for rarity, info in classic_roster:
        chars = info["char_id_lst"].copy()
        if rarity in cutoffs:
            chars = chars[: chars.index(cutoffs[rarity]) + 1]
        result[rarity] = {"char_id_lst": chars, "total_percent": info["total_percent"]}
    return result


def _unlock_entry_if_eligible(player_data, release, changes):
    entry = release["entryStageId"]
    condition = const_json_loader[STAGE_TABLE]["stages"][entry]["unlockCondition"].copy()
    stages = player_data["dungeon"]["stages"]
    if all(item["stageId"] in stages and stages[item["stageId"]]["state"] >= 2
           for item in condition) and entry not in stages:
        stages[entry] = _stage_record(entry)
        changes.append(f"dungeon.stages.{entry}")


def reconcile_historical_release(player_data):
    """Materialize supported scheduled/permanent content without erasing state."""
    changes = []
    active_activities = active_activity_ids(player_data)
    active_pools = active_pool_ids(player_data)
    for _, release in release_definitions():
        limited_active = release["activityId"] in active_activities
        permanent_open = retro_is_unlocked(player_data, release["retroId"])
        if limited_active:
            if release["activityType"] not in player_data["activity"] or player_data["activity"][release["activityType"]] is None:
                player_data["activity"][release["activityType"]] = {}
            group = player_data["activity"][release["activityType"]]
            if release["activityId"] not in group:
                group[release["activityId"]] = {"milestone": {"points": {}, "rewards": {}}}
                changes.append(f"activity.{release['activityType']}.{release['activityId']}")
        if limited_active or permanent_open:
            _unlock_entry_if_eligible(player_data, release, changes)
        for pool_id in release["poolIds"].copy():
            if pool_id in active_pools and pool_id not in player_data["gacha"]["normal"]:
                player_data["gacha"]["normal"][pool_id] = {"cnt": 0, "maxCnt": 10, "rarity": 4, "avail": True}
                changes.append(f"gacha.normal.{pool_id}")
    # Standard rotations are independent of activities.  Materialize every
    # audited pool in the selected date window so the client's guarantee UI
    # has a record even before the first pull.
    for pool_id in sorted(active_pools):
        if pool_id in HISTORICAL_POOL_RULES and pool_id not in player_data["gacha"]["normal"]:
            player_data["gacha"]["normal"][pool_id] = {
                "cnt": 0, "maxCnt": 10, "rarity": 4, "avail": True,
            }
            changes.append(f"gacha.normal.{pool_id}")
    return changes


def unlock_retro_block(player_data, retro_id):
    if retro_id not in permanent_retro_ids():
        return "retro_not_available", []
    blocks = player_data["retro"]["block"]
    if retro_id in blocks and blocks[retro_id]["open"] == 1:
        return None, []
    cost = int(const_json_loader[RETRO_TABLE]["retroUnlockCost"])
    if int(player_data["retro"]["coin"]) < cost:
        return "insufficient_retro_coin", []
    player_data["retro"]["coin"] -= cost
    blocks[retro_id] = {"locked": 0, "open": 1}
    changes = [f"retro.block.{retro_id}", "retro.coin"]
    for _, release in release_definitions():
        if release["retroId"] == retro_id:
            _unlock_entry_if_eligible(player_data, release, changes)
    return None, changes


def historical_stage_rule(stage_id):
    stage = const_json_loader[STAGE_TABLE]["stages"][stage_id]
    first_pass = [{"type": "CHAR", "id": "char_325_bison", "count": 1}] if stage_id == "act5d0_05" else []
    return {
        "stageId": stage_id, "stageType": stage["stageType"], "zoneId": stage["zoneId"],
        "difficulty": stage["difficulty"], "settlementEnabled": True,
        "ruleCompleteness": "historical_deterministic_no_random_drops",
        "apCost": stage["apCost"], "apFailReturn": stage["apFailReturn"],
        "expGain": stage["expGain"], "goldGain": stage["goldGain"],
        "diamondOnceDrop": stage["diamondOnceDrop"],
        "unlockCondition": stage["unlockCondition"].copy(), "firstPassRewards": first_pass,
        "unconfirmedFirstPassRewards": [], "dropRules": [], "missions": [],
        "sources": {"deterministic": "stage_table and explicit ONCE CHAR display record",
                    "random": "not implemented for this historical release"},
    }


def complete_historical_story_stage(player_data, story_id):
    active = active_activity_ids(player_data)
    release = next((item for _, item in release_definitions()
                    if story_id.startswith(f"activities/{item['activityId']}/level_")
                    and (item["activityId"] in active or retro_is_unlocked(player_data, item["retroId"]))), None)
    if not release:
        return None
    stage_id = story_id.rsplit("/level_", 1)[-1]
    if stage_id not in release_stage_ids(release) or "_st" not in stage_id:
        return None
    stages = player_data["dungeon"]["stages"]
    if stage_id not in stages:
        return {"stageId": stage_id, "changed": False, "reason": "stage_not_unlocked"}
    record = stages[stage_id]
    changed = record["state"] < 3
    if changed:
        record["state"] = 3
        record["completeTimes"] += 1
    unlocked = []
    for candidate_id in release_stage_ids(release):
        if candidate_id in stages:
            continue
        conditions = const_json_loader[STAGE_TABLE]["stages"][candidate_id]["unlockCondition"].copy()
        if conditions and all(item["stageId"] in stages and stages[item["stageId"]]["state"] >= 2 for item in conditions):
            stages[candidate_id] = _stage_record(candidate_id)
            unlocked.append(candidate_id)
    return {"stageId": stage_id, "changed": changed, "unlockStages": sorted(unlocked)}
