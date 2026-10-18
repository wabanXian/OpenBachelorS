"""Resource-driven visibility lifecycle for persistent player missions."""

from ..const.filepath import MISSION_TABLE
from .const_json_loader import const_json_loader


VISIBLE_TYPES = {"MAIN", "SUB", "GUIDE"}
STAGE_TEMPLATES = {"CompleteMainStage", "CompleteStage"}
TARGET_AT_END_TEMPLATES = STAGE_TEMPLATES | {
    "CompleteAnyStage",
    "UpgradeChar",
    "UpgradePlayer",
}


def _target(resource):
    params = resource["param"].copy() if resource["param"] is not None else []
    # Implemented progress schemas store their required count last. Most other
    # templates store the count in param[1] and may put an item/room identifier
    # after it; that identifier must not become the visible progress target.
    candidates = (
        list(reversed(params))
        if resource["template"] in TARGET_AT_END_TEMPLATES
        else params[1:2]
    )
    for value in candidates:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            continue
    return 1


def _stage_condition_met(player_data, resource):
    condition = resource["unlockCondition"]
    if condition is None:
        return True
    if condition != "StageCondition":
        return False
    params = resource["unlockParam"].copy() if resource["unlockParam"] is not None else []
    if len(params) < 2:
        return False
    try:
        required_state = int(params[0])
    except (TypeError, ValueError):
        return False
    stage_id = params[1]
    stages = player_data["dungeon"]["stages"]
    return stage_id in stages and stages[stage_id]["state"] >= required_state


def _mission_claimed(player_data, mission_id, resources):
    if mission_id not in resources:
        return False
    mission_type = resources[mission_id]["type"]
    missions = player_data["mission"]["missions"]
    return (
        mission_type in missions
        and mission_id in missions[mission_type]
        and missions[mission_type][mission_id]["state"] == 3
    )


def _group_complete(player_data, group, resources):
    mission_ids = group["missionIds"].copy() if group["missionIds"] is not None else []
    return bool(mission_ids) and all(
        _mission_claimed(player_data, mission_id, resources)
        for mission_id in mission_ids
    )


def _group_unlocked(player_data, group, groups, resources):
    # Existing saves and older settlement code may already have exposed a
    # group. Never hide it merely because its historical predecessor record is
    # absent.
    if group["id"] in player_data["mission"]["missionGroups"]:
        return True
    predecessor = group["preMissionGroup"]
    if predecessor is None:
        return True
    if predecessor not in groups:
        return False
    group_states = player_data["mission"]["missionGroups"]
    return (
        predecessor in group_states and group_states[predecessor] == 1
    ) or _group_complete(player_data, groups[predecessor], resources)


def _stage_progress(player_data, resource, target):
    if resource["template"] not in STAGE_TEMPLATES:
        return 0
    params = resource["param"].copy() if resource["param"] is not None else []
    if len(params) < 2:
        return 0
    stage_id = params[-2]
    stages = player_data["dungeon"]["stages"]
    return target if stage_id in stages and stages[stage_id]["state"] >= 2 else 0


def reconcile_visible_missions(player_data):
    """Expose unlocked missions without resetting any persisted progress."""
    table = const_json_loader[MISSION_TABLE]
    resources = table["missions"]
    groups = table["missionGroups"]
    changes = []

    made_progress = True
    while made_progress:
        made_progress = False
        for group_id, group in groups:
            mission_type = group["type"]
            if mission_type not in VISIBLE_TYPES:
                continue
            group_states = player_data["mission"]["missionGroups"]
            # A historical claim implementation marked a GUIDE group as 1
            # whenever any one mission was claimed. Real syncData evidence
            # keeps an active GUIDE group at 0 while its missions are visible.
            # Repair only the provably inconsistent case: the group is marked
            # finished although not every member mission has been claimed.
            if (
                mission_type == "GUIDE"
                and group_id in group_states
                and group_states[group_id] == 1
                and not _group_complete(player_data, group, resources)
            ):
                group_states[group_id] = 0
                changes.append(
                    {"kind": "guide_group_reactivated", "groupId": group_id}
                )
                made_progress = True
            if not _group_unlocked(player_data, group, groups, resources):
                continue
            if group_id not in group_states:
                group_states[group_id] = 0
                changes.append({"kind": "group_exposed", "groupId": group_id})
                made_progress = True

            mission_ids = (
                group["missionIds"].copy() if group["missionIds"] is not None else []
            )
            for mission_id in mission_ids:
                resource = resources[mission_id]
                predecessors = (
                    resource["preMissionIds"].copy()
                    if resource["preMissionIds"] is not None
                    else []
                )
                if not all(
                    _mission_claimed(player_data, predecessor, resources)
                    for predecessor in predecessors
                ):
                    continue
                if not _stage_condition_met(player_data, resource):
                    continue
                missions = player_data["mission"]["missions"][mission_type]
                target = _target(resource)
                current = _stage_progress(player_data, resource, target)
                if mission_id not in missions:
                    missions[mission_id] = {
                        "progress": [{"target": target, "value": current}],
                        # Client/reference syncData evidence uses state 2 for
                        # visible missions even before progress reaches target.
                        # Claimability is determined by the progress array.
                        "state": 2,
                    }
                    changes.append(
                        {
                            "kind": "mission_exposed",
                            "missionId": mission_id,
                            "state": missions[mission_id]["state"],
                        }
                    )
                    made_progress = True
                elif missions[mission_id]["state"] in (1, 2):
                    progress = missions[mission_id]["progress"][0]
                    if missions[mission_id]["state"] == 1 or current > progress["value"]:
                        new_value = min(current, progress["target"])
                        missions[mission_id] = {
                            "progress": [
                                {"target": progress["target"], "value": new_value}
                            ],
                            "state": 2,
                        }
                        changes.append(
                            {
                                "kind": "mission_progress_reconciled",
                                "missionId": mission_id,
                                "state": missions[mission_id]["state"],
                            }
                        )

            if mission_type in {"MAIN", "SUB"} and _group_complete(
                player_data, group, resources
            ):
                if group_states[group_id] != 1:
                    group_states[group_id] = 1
                    changes.append({"kind": "group_completed", "groupId": group_id})
                    made_progress = True
    return changes


def update_has_room_missions(player_data):
    """Refresh already-visible resource-backed ``HasRoom`` mission progress."""
    reconcile_visible_missions(player_data)
    resources = const_json_loader[MISSION_TABLE]["missions"]
    rooms = player_data["building"]["roomSlots"].copy().values()
    changed = []
    for mission_type in VISIBLE_TYPES:
        visible = player_data["mission"]["missions"][mission_type]
        for mission_id, mission in visible.copy().items():
            if mission_id not in resources:
                continue
            resource = resources[mission_id].copy()
            if resource.get("template") != "HasRoom" or mission.get("state") == 3:
                continue
            params = resource.get("param") or []
            if len(params) < 4:
                continue
            try:
                target = max(1, int(params[1]))
                minimum_level = int(params[2])
            except (TypeError, ValueError):
                continue
            room_type = params[3]
            current = min(
                target,
                sum(
                    1
                    for room in rooms
                    if room.get("roomId") == room_type
                    and int(room.get("level", 0)) >= minimum_level
                ),
            )
            before = mission["progress"][0]["value"]
            if current == before:
                continue
            visible[mission_id] = {
                "progress": [{"target": target, "value": current}],
                "state": 2,
            }
            changed.append({"missionId": mission_id, "before": before, "after": current})
    return changed
