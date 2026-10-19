"""Resource-driven mission lifecycle and verified gameplay events."""

from datetime import datetime, timedelta, timezone

from ..const.filepath import CHARACTER_TABLE, MISSION_TABLE
from .const_json_loader import const_json_loader
from .faketime import faketime
from .favor import favor_percent

VISIBLE_TYPES = {"MAIN", "SUB", "GUIDE", "DAILY", "WEEKLY"}
PERIODICAL_TYPES = {"DAILY", "WEEKLY"}
CN_TZ = timezone(timedelta(hours=8))
PERIODIC_UNLOCK_PROGRESS = 1000  # obt/guide/l0-9/0_mission_others
_TRANSIENT_PERIODS = {}


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def mission_spec(resource):
    """Decode parameters only for templates with evidence in GameData."""
    if not isinstance(resource, dict):
        resource = resource.copy()
    template = resource["template"]
    params = resource["param"].copy() if resource["param"] is not None else []
    ttype = str(resource.get("templateType", ""))
    spec = {"template": template, "supported": True, "mode": "event", "target": 1}
    if template in {"CompleteMainStage", "CompleteStage"}:
        if template == "CompleteStage" and ttype == "2":
            spec.update(event="battle_success", minStars=_int(params[1], 1), target=_int(params[2], 1))
        elif len(params) >= 2:
            spec.update(event="battle_success", stageId=params[-2], target=_int(params[-1], 1))
        else:
            spec["supported"] = False
    elif template == "CompleteAnyStage":
        if len(params) >= 3:
            spec.update(event="battle_success", stageIds=set(str(params[1]).split("^")), minStars=_int(params[2], 2), target=1)
        else:
            spec["supported"] = False
    elif template == "CompleteStageAnyType":
        spec.update(event="battle_success", target=_int(params[1], 1), minStars=_int(params[2], 2))
    elif template == "EnemyKillInAnyStage":
        spec.update(event="battle_result", metric="killCnt", target=_int(params[1], 1))
    elif template == "StageWithEnemyKill":
        # Requires boss-kill evidence, which current decoded battle samples do
        # not expose separately from total killCnt.
        spec["supported"] = False
    elif template == "BattleWithEnemyKill":
        spec.update(event="battle_success", stageId=params[1] if len(params) > 1 else None, minKills=_int(params[2] if len(params) > 2 else 0), target=1)
    elif template == "StageWithAssistChar":
        spec.update(event="battle_success", requireAssist=True, target=_int(params[-1], 1))
    elif template == "StageWithReplay":
        spec.update(event="battle_success", requireReplay=True, target=_int(params[-1], 1))
    elif template == "UpgradeChar":
        if ttype == "0" or len(params) == 2:
            spec.update(event="upgrade_char", target=_int(params[1], 1))
        else:
            spec.update(mode="state", state="operator_level", target=_int(params[1], 1), phase=_int(params[-2]), level=_int(params[-1], 1))
    elif template == "UpgradePlayer":
        spec.update(mode="state", state="player_level", requiredLevel=_int(params[-1], 1), target=1)
    elif template == "SquadFormation":
        spec.update(event="squad_formation", professions=set(str(params[1]).split("#")), target=_int(params[-1], 1))
    elif template == "HasChar":
        if len(params) >= 6:
            spec.update(mode="state", state="has_char", target=_int(params[1], 1), phase=_int(params[2]), level=_int(params[3], 1), rarity=_int(params[4]), profession=params[5])
        else:
            spec["supported"] = False
    elif template == "HasRoom":
        spec.update(mode="state", state="has_room", target=_int(params[1], 1), level=_int(params[2]), roomType=params[3])
    elif template == "NormalGacha":
        spec.update(event="normal_gacha", target=_int(params[-1], 1))
    elif template == "DeliveryOrder":
        spec.update(event="delivery_order", target=_int(params[-1], 1))
    elif template == "ManufactureItem":
        spec.update(event="manufacture_item", target=_int(params[-1], 1))
    elif template == "ReceiveSocialPoint":
        spec.update(event="receive_social_point", target=_int(params[-1], 1))
    elif template == "BuyShopItem":
        spec.update(event="buy_shop_item", target=_int(params[-1], 1))
    elif template == "GainIntimacy":
        spec.update(event="gain_intimacy", target=_int(params[-1], 1))
    elif template == "RecoverCharBaseAp":
        spec.update(event="recover_char_ap", target=_int(params[-1], 1))
    elif template == "VisitBuilding":
        spec.update(event="visit_building", target=_int(params[-1], 1))
    elif template == "EvolveChar":
        spec.update(mode="state", state="evolve_char", target=_int(params[1], 1), phase=_int(params[2], 1))
    elif template == "BoostPotential":
        spec.update(mode="state", state="potential", target=_int(params[1], 1), potential=_int(params[2], 1))
    elif template == "UpgradeSkill" and ttype == "1":
        spec.update(mode="state", state="skill_rank", target=1, rank=_int(params[1], 1))
    elif template == "UpgradeSkill":
        spec.update(event="upgrade_skill", target=_int(params[-1], 1))
    elif template == "CharIntimacy":
        spec.update(mode="state", state="char_intimacy", target=_int(params[1], 1), percent=_int(params[2], 1))
    else:
        spec["supported"] = False
    spec["target"] = max(1, _int(spec.get("target"), 1))
    return spec


def _claimed(player_data, mission_id, resources):
    if mission_id not in resources:
        return False
    mtype = resources[mission_id]["type"]
    missions = player_data["mission"]["missions"]
    return mtype in missions and mission_id in missions[mtype] and missions[mtype][mission_id]["state"] == 3


def _group_complete(player_data, group, resources):
    ids = group["missionIds"].copy() if group["missionIds"] is not None else []
    return bool(ids) and all(_claimed(player_data, mid, resources) for mid in ids)


def _stage_condition_met(player_data, resource):
    condition = resource["unlockCondition"]
    if condition is None:
        return True
    if condition != "StageCondition":
        return False
    params = resource["unlockParam"].copy() if resource["unlockParam"] is not None else []
    if len(params) < 2:
        return False
    stages, stage_id = player_data["dungeon"]["stages"], params[1]
    return stage_id in stages and stages[stage_id]["state"] >= _int(params[0])


def _group_unlocked(player_data, group, groups, resources):
    if group["id"] in player_data["mission"]["missionGroups"]:
        return True
    predecessor = group["preMissionGroup"]
    return predecessor is None or (predecessor in groups and _group_complete(player_data, groups[predecessor], resources))


def _period_keys(now=None):
    current = datetime.fromtimestamp(faketime() if now is None else now, CN_TZ)
    logical = current - timedelta(hours=4)
    day = logical.date().isoformat()
    monday = (logical.date() - timedelta(days=logical.weekday())).isoformat()
    return day, monday, logical.isoweekday(), int(current.timestamp())


def active_periodic_groups(now=None):
    table = const_json_loader[MISSION_TABLE]
    _, _, weekday, ts = _period_keys(now)
    daily = daily_reward = None
    for _, period in table["dailyMissionPeriodInfo"]:
        if period["startTime"] <= ts <= period["endTime"]:
            for _, row in period["periodList"]:
                if weekday in row["period"].copy():
                    daily, daily_reward = row["missionGroupId"], row["rewardGroupId"]
                    break
    weekly = next((gid for gid, group in table["missionGroups"] if group["type"] == "WEEKLY" and group["startTs"] <= ts <= group["endTs"]), None)
    weekly_reward = next((reward["groupId"] for _, reward in table["weeklyRewards"] if reward["beginTime"] <= ts <= reward["endTime"]), None)
    return {"DAILY": (daily, daily_reward), "WEEKLY": (weekly, weekly_reward)}


def _periodic_reward_ids(mission_type, group_id):
    if group_id is None:
        return []
    table = const_json_loader[MISSION_TABLE]
    rows = (
        table["periodicalRewards"]
        if mission_type == "DAILY"
        else table["weeklyRewards"]
    )
    return [row["id"] for _, row in rows if row["groupId"] == group_id]


def _state_value(player_data, spec):
    if spec.get("state") == "player_level":
        return 1 if player_data["status"]["level"] >= spec["requiredLevel"] else 0
    if spec.get("state") == "operator_level":
        count = sum(1 for _, char in player_data["troop"]["chars"] if char["evolvePhase"] >= spec["phase"] and char["level"] >= spec["level"])
        return min(spec["target"], count)
    if spec.get("state") == "evolve_char":
        return min(spec["target"], sum(1 for _, char in player_data["troop"]["chars"] if char["evolvePhase"] >= spec["phase"]))
    if spec.get("state") == "has_char":
        chars = const_json_loader[CHARACTER_TABLE]
        count = 0
        for _, char in player_data["troop"]["chars"]:
            resource = chars[char["charId"]]
            if (
                char["evolvePhase"] >= spec["phase"]
                and char["level"] >= spec["level"]
                and resource["rarity"] == f"TIER_{spec['rarity']}"
                and (spec["profession"] == "ALL" or resource["profession"] == spec["profession"])
            ):
                count += 1
        return min(spec["target"], count)
    if spec.get("state") == "potential":
        return min(spec["target"], sum(1 for _, char in player_data["troop"]["chars"] if char["potentialRank"] + 1 >= spec["potential"]))
    if spec.get("state") == "skill_rank":
        return 1 if any(char["mainSkillLvl"] >= spec["rank"] for _, char in player_data["troop"]["chars"]) else 0
    if spec.get("state") == "char_intimacy":
        return min(spec["target"], sum(1 for _, char in player_data["troop"]["chars"] if favor_percent(char["favorPoint"]) >= spec["percent"]))
    if spec.get("state") == "has_room":
        count = sum(1 for _, room in player_data["building"]["roomSlots"] if room.get("roomId") == spec["roomType"] and _int(room.get("level")) >= spec["level"])
        return min(spec["target"], count)
    return 0


def _persisted_event_value(player_data, spec):
    """Derive only durable one-shot stage facts during login reconciliation."""
    stage_id = spec.get("stageId")
    if not stage_id:
        return 0
    stages = player_data["dungeon"]["stages"]
    if stage_id not in stages or stages[stage_id]["state"] < max(2, spec.get("minStars", 0)):
        return 0
    return spec["target"]


def _record(target, value=0):
    # The current PC list only renders states 2 and 3.  State 2 remains visible
    # while progress distinguishes unfinished from claimable; state 3 is the
    # post-claim presentation.
    return {"progress": [{"target": target, "value": min(target, value)}], "state": 2}


def refresh_periodic_missions(player_data, now=None):
    mission = player_data["mission"]
    day, week, _, _ = _period_keys(now)
    extra = getattr(player_data, "extra_save", None)
    marker = (extra.save_obj.get("mission_periods_v1", {}) if extra is not None else _TRANSIENT_PERIODS.get(id(player_data), {})) or {}
    active, changes = active_periodic_groups(now), []
    if marker.get("daily") != day:
        old_group = marker.get("dailyGroup")
        if old_group and old_group in mission["missionGroups"]:
            del mission["missionGroups"][old_group]
        for mission_id, _ in mission["missions"]["DAILY"].copy().items():
            del mission["missions"]["DAILY"][mission_id]
        mission["missionRewards"]["dailyPoint"] = 0
        mission["missionRewards"]["rewards"]["DAILY"] = {}
        changes.append({"kind": "daily_reset", "period": day})
    if marker.get("weekly") != week:
        old_group = marker.get("weeklyGroup")
        if old_group and old_group in mission["missionGroups"]:
            del mission["missionGroups"][old_group]
        for mission_id, _ in mission["missions"]["WEEKLY"].copy().items():
            del mission["missions"]["WEEKLY"][mission_id]
        mission["missionRewards"]["weeklyPoint"] = 0
        mission["missionRewards"]["rewards"]["WEEKLY"] = {}
        changes.append({"kind": "weekly_reset", "period": week})
    # The PC reward-list parser enumerates this dictionary.  An empty mapping
    # produces an empty milestone list and leaves its allClear accumulator true,
    # which displays the full-page "reward already received" stamp.  Resource
    # rows therefore need explicit 0 (unclaimed) states from the start.
    for mission_type, reward_group in (
        ("DAILY", active["DAILY"][1]),
        ("WEEKLY", active["WEEKLY"][1]),
    ):
        reward_states = mission["missionRewards"]["rewards"][mission_type]
        for reward_id in _periodic_reward_ids(mission_type, reward_group):
            if reward_id not in reward_states:
                reward_states[reward_id] = 0
                changes.append(
                    {
                        "kind": "periodic_reward_state_initialized",
                        "type": mission_type,
                        "rewardId": reward_id,
                    }
                )
    new_marker = {"daily": day, "weekly": week, "dailyGroup": active["DAILY"][0], "weeklyGroup": active["WEEKLY"][0], "dailyRewardGroup": active["DAILY"][1], "weeklyRewardGroup": active["WEEKLY"][1]}
    if extra is not None:
        extra.save_obj["mission_periods_v1"] = new_marker
    else:
        _TRANSIENT_PERIODS[id(player_data)] = new_marker
    return changes


def reconcile_visible_missions(player_data, now=None):
    table = const_json_loader[MISSION_TABLE]
    resources, groups = table["missions"], table["missionGroups"]
    changes = refresh_periodic_missions(player_data, now)
    active = active_periodic_groups(now)
    periodic_unlocked = player_data["status"]["progress"] >= PERIODIC_UNLOCK_PROGRESS
    made_progress = True
    while made_progress:
        made_progress = False
        for group_id, group in groups:
            mtype = group["type"]
            if mtype not in VISIBLE_TYPES:
                continue
            if mtype in PERIODICAL_TYPES and (not periodic_unlocked or group_id != active[mtype][0]):
                continue
            if not _group_unlocked(player_data, group, groups, resources):
                continue
            group_states = player_data["mission"]["missionGroups"]
            if mtype == "GUIDE" and group_id in group_states and group_states[group_id] == 1 and not _group_complete(player_data, group, resources):
                group_states[group_id] = 0
                changes.append({"kind": "guide_group_reactivated", "groupId": group_id})
            if group_id not in group_states:
                group_states[group_id] = 0
                changes.append({"kind": "group_exposed", "groupId": group_id})
                made_progress = True
            missions = player_data["mission"]["missions"][mtype]
            mission_ids = group["missionIds"].copy() if group["missionIds"] is not None else []
            for mission_id in mission_ids:
                resource = resources[mission_id]
                predecessors = resource["preMissionIds"].copy() if resource["preMissionIds"] is not None else []
                if not all(_claimed(player_data, p, resources) for p in predecessors) or not _stage_condition_met(player_data, resource):
                    continue
                spec, current = mission_spec(resource), None
                if spec.get("mode") == "state":
                    current = _state_value(player_data, spec)
                elif spec.get("stageId"):
                    current = _persisted_event_value(player_data, spec)
                if mission_id not in missions:
                    missions[mission_id] = _record(spec["target"], current or 0)
                    changes.append({"kind": "mission_exposed", "missionId": mission_id, "supported": spec["supported"]})
                    made_progress = True
                elif missions[mission_id]["state"] == 1:
                    record = missions[mission_id].copy()
                    record["state"] = 2
                    missions[mission_id] = record
                    changes.append(
                        {"kind": "mission_progress_reconciled", "missionId": mission_id}
                    )
                elif missions[mission_id]["state"] != 3 and current is not None:
                    record = missions[mission_id].copy()
                    rows = record["progress"].copy()
                    row = rows[0]
                    if current > row["value"]:
                        row["value"] = min(row["target"], current)
                        record["progress"] = rows
                        record["state"] = 2
                        missions[mission_id] = record
                        changes.append({"kind": "mission_state_reconciled", "missionId": mission_id})
            if mtype in {"MAIN", "SUB"} and _group_complete(player_data, group, resources) and group_states[group_id] != 1:
                group_states[group_id] = 1
                changes.append({"kind": "group_completed", "groupId": group_id})
                made_progress = True
    return changes


def _matches(spec, event_type, payload):
    if not spec["supported"] or spec.get("mode") != "event" or spec.get("event") != event_type:
        return False
    if spec.get("stageId") and payload.get("stageId") != spec["stageId"]:
        return False
    if spec.get("stageIds") and payload.get("stageId") not in spec["stageIds"]:
        return False
    if payload.get("stars", 0) < spec.get("minStars", 0) or payload.get("killCnt", 0) < spec.get("minKills", 0):
        return False
    if spec.get("requireAssist") and not payload.get("usedAssist"):
        return False
    if spec.get("requireReplay") and not payload.get("isReplay"):
        return False
    if spec.get("professions") and not spec["professions"].issubset(set(payload.get("professions", []))):
        return False
    return True


def apply_mission_event(player_data, event_type, payload, event_id=None, now=None):
    """Apply one successful server operation to every matching visible row."""
    reconcile_visible_missions(player_data, now)
    resources, changed = const_json_loader[MISSION_TABLE]["missions"], []
    for mtype in VISIBLE_TYPES:
        for mission_id, record in player_data["mission"]["missions"][mtype]:
            if record["state"] == 3 or mission_id not in resources:
                continue
            spec = mission_spec(resources[mission_id])
            if not _matches(spec, event_type, payload):
                continue
            editable = record.copy()
            rows = editable["progress"].copy()
            row, before = rows[0], rows[0]["value"]
            amount = max(0, _int(payload.get(spec.get("metric", "count"), 1), 1))
            row["value"] = min(row["target"], before + amount)
            if row["value"] != before:
                editable["progress"] = rows
                editable["state"] = 2
                player_data["mission"]["missions"][mtype][mission_id] = editable
                changed.append({"missionId": mission_id, "type": mtype, "before": before, "after": row["value"], "eventId": event_id})
    return changed


def plan_mission_events(player_data, events, event_id=None):
    """Return absolute mission mutations for an atomic settlement journal."""
    resources = const_json_loader[MISSION_TABLE]["missions"]
    planned, changed = {}, []
    for event_type, payload in events:
        candidates = []
        for mtype in VISIBLE_TYPES:
            candidates.extend(
                (mtype, mission_id, record)
                for mission_id, record in player_data["mission"]["missions"][mtype]
            )
        # A stage-completion mission may become visible because of this very
        # settlement. Materialise matching MAIN/SUB rows in the same journal
        # instead of relying on the former rule-specific "set completed" path.
        existing = {(mtype, mission_id) for mtype, mission_id, _ in candidates}
        for mission_id, resource in resources:
            mtype = resource["type"]
            if mtype not in {"MAIN", "SUB"} or (mtype, mission_id) in existing:
                continue
            spec = mission_spec(resource)
            if _matches(spec, event_type, payload):
                candidates.append((mtype, mission_id, _record(spec["target"])))

        for mtype, mission_id, record in candidates:
                if record["state"] == 3 or mission_id not in resources:
                    continue
                spec = mission_spec(resources[mission_id])
                if not _matches(spec, event_type, payload):
                    continue
                key = (mtype, mission_id)
                editable = planned.get(key, record.copy())
                rows = editable["progress"].copy()
                row, before = rows[0], rows[0]["value"]
                amount = max(0, _int(payload.get(spec.get("metric", "count"), 1), 1))
                row["value"] = min(row["target"], before + amount)
                if row["value"] == before:
                    continue
                editable["progress"] = rows
                editable["state"] = 2
                planned[key] = editable
                changed.append({"missionId": mission_id, "type": mtype, "before": before, "after": row["value"], "eventId": event_id})
    mutations = [
        {"path": ["mission", "missions", mtype, mission_id], "value": value}
        for (mtype, mission_id), value in planned.items()
    ]
    groups = const_json_loader[MISSION_TABLE]["missionGroups"]
    for mtype, mission_id in planned:
        group_id = resources[mission_id]["missionGroup"]
        if group_id in groups:
            mutations.append(
                {"path": ["mission", "missionGroups", group_id], "value": 0}
            )
    return mutations, changed


def update_has_room_missions(player_data):
    before = {mid: rec["progress"][0]["value"] for mtype in VISIBLE_TYPES for mid, rec in player_data["mission"]["missions"][mtype]}
    reconcile_visible_missions(player_data)
    return [{"missionId": mid, "before": before.get(mid, 0), "after": rec["progress"][0]["value"]} for mtype in VISIBLE_TYPES for mid, rec in player_data["mission"]["missions"][mtype] if rec["progress"][0]["value"] != before.get(mid, 0)]
