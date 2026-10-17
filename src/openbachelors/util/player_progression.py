"""Resource-driven Doctor EXP, level and sanity progression."""

from ..const.filepath import GAMEDATA_CONST, MISSION_TABLE
from .const_json_loader import const_json_loader


def calculate_player_progression(level, exp, ap, exp_gain):
    """Return normalized progression without mutating player data.

    ``status.exp`` is progress within the current level. Each crossed level
    consumes the corresponding ``playerExpMap`` entry. Level-up sanity is an
    additive direct gain and can exceed ``maxAp``.
    """
    game_const = const_json_loader[GAMEDATA_CONST]
    exp_map = [int(value) for value in game_const["playerExpMap"].copy()]
    ap_map = [int(value) for value in game_const["playerApMap"].copy()]
    max_level = min(int(game_const["maxPlayerLevel"]), len(exp_map), len(ap_map))
    level = min(max(1, int(level)), max_level)
    exp = max(0, int(exp))
    ap = max(0, int(ap))
    exp_gain = max(0, int(exp_gain))
    before = {"level": level, "exp": exp, "ap": ap}
    level_ups = []

    if level >= max_level:
        exp = 0
    else:
        exp += exp_gain
        while level < max_level:
            threshold = exp_map[level - 1]
            if exp < threshold:
                break
            exp -= threshold
            level += 1
            new_max_ap = ap_map[level - 1]
            ap += new_max_ap
            level_ups.append(
                {
                    "level": level,
                    "threshold": threshold,
                    "maxAp": new_max_ap,
                    "apGranted": new_max_ap,
                }
            )
        if level >= max_level:
            exp = 0

    result = {
        "level": level,
        "exp": exp,
        "ap": ap,
        "maxAp": ap_map[level - 1],
        "maxLevel": max_level,
        "levelUps": level_ups,
        "expGainApplied": 0 if before["level"] >= max_level else exp_gain,
        "before": before,
    }
    return result


def _stage_condition_met(player_data, mission):
    condition = mission.get("unlockCondition")
    if condition is None:
        return True
    if condition != "StageCondition":
        return False
    params = mission.get("unlockParam") or []
    if len(params) < 2:
        return False
    required_state = int(params[0])
    stage_id = params[1]
    stages = player_data["dungeon"]["stages"]
    return stage_id in stages and int(stages[stage_id]["state"]) >= required_state


def plan_player_exp_gain(player_data, exp_gain):
    """Build absolute settlement mutations, including confirmed level tasks."""
    status = player_data["status"]
    result = calculate_player_progression(
        status["level"], status["exp"], status["ap"], exp_gain
    )
    mutations = []
    for field in ("level", "exp", "ap", "maxAp"):
        if status[field] != result[field]:
            mutations.append(
                {"path": ["status", field], "value": result[field]}
            )

    mission_updates = []
    mission_table = const_json_loader[MISSION_TABLE]["missions"]
    guide_missions = player_data["mission"]["missions"]["GUIDE"]
    for mission_id, mission_view in mission_table:
        mission = mission_view.copy()
        if mission.get("type") != "GUIDE" or mission.get("template") != "UpgradePlayer":
            continue
        params = mission.get("param") or []
        if len(params) < 2 or result["level"] < int(params[1]):
            continue
        if not _stage_condition_met(player_data, mission):
            continue
        if mission_id in guide_missions and guide_missions[mission_id]["state"] in (2, 3):
            continue
        mutations.extend(
            [
                {
                    "path": ["mission", "missions", "GUIDE", mission_id],
                    "value": {
                        "progress": [{"target": 1, "value": 1}],
                        "state": 2,
                    },
                },
                {
                    "path": ["mission", "missionGroups", mission["missionGroup"]],
                    "value": 0,
                },
            ]
        )
        mission_updates.append(mission_id)
    result["missionUpdates"] = mission_updates
    return mutations, result
