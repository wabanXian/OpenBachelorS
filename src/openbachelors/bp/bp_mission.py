"""Resource-driven claiming for completed ordinary main-stage missions."""

from fastapi import APIRouter, Request

from ..const.filepath import MISSION_TABLE
from ..util.const_json_loader import const_json_loader
from ..util.player_data import player_data_decorator
from ..util.tutorial_debug import event
from ..util.reward_grant import apply_reward_immediate, reward_target_path
from ..util.mission_lifecycle import reconcile_visible_missions


router = APIRouter()


SUPPORTED_MISSION_TYPES = {"MAIN", "SUB", "GUIDE"}


def _mission_record(player_data, mission_id):
    for _, missions in player_data["mission"]["missions"]:
        if mission_id in missions:
            return missions[mission_id]
    return None


def _grant_mission_rewards(player_data, rewards):
    granted = []
    for reward in rewards:
        granted.append(apply_reward_immediate(player_data, reward))
    return granted


def _claim_mission(player_data, mission_id, expected_type=None):
    """Validate and claim one mission inside the caller's single save unit."""
    resources = const_json_loader[MISSION_TABLE]["missions"]
    resource = resources[mission_id] if mission_id in resources else None
    mission_type = resource["type"] if resource is not None else None
    missions = (
        player_data["mission"]["missions"][mission_type]
        if mission_type in SUPPORTED_MISSION_TYPES
        else None
    )
    if expected_type is not None and mission_type != expected_type:
        return {"missionId": mission_id, "items": [], "reason": "mission_type_mismatch"}
    if resource is None or missions is None or mission_id not in missions:
        return {
            "missionId": mission_id,
            "items": [],
            "reason": "unsupported_or_missing_mission",
        }
    mission = missions[mission_id]
    complete = all(
        progress["value"] >= progress["target"]
        for _, progress in mission["progress"]
    )
    if mission["state"] == 3:
        return {"missionId": mission_id, "items": [], "reason": "already_claimed"}
    if mission["state"] != 2 or not complete:
        return {"missionId": mission_id, "items": [], "reason": "mission_not_complete"}
    if any(
        (_mission_record(player_data, predecessor) is None)
        or _mission_record(player_data, predecessor)["state"] != 3
        for predecessor in (
            resource["preMissionIds"].copy()
            if resource["preMissionIds"] is not None
            else []
        )
    ):
        return {"missionId": mission_id, "items": [], "reason": "pre_mission_not_claimed"}

    rewards = resource["rewards"].copy()
    unsupported = [item["type"] for item in rewards if reward_target_path(item) is None]
    if unsupported:
        return {
            "missionId": mission_id,
            "items": [],
            "reason": "unsupported_reward_type",
            "unsupportedRewardTypes": sorted(set(unsupported)),
        }

    items = _grant_mission_rewards(player_data, rewards)
    mission["state"] = 3
    lifecycle_changes = reconcile_visible_missions(player_data)
    return {
        "missionId": mission_id,
        "items": items,
        "reason": None,
        "lifecycleChanges": lifecycle_changes,
    }


@router.post("/mission/confirmMission")
@player_data_decorator
async def mission_confirmMission(player_data, request: Request):
    request_json = await request.json()
    mission_id = request_json.get("missionId")
    result = _claim_mission(player_data, mission_id)

    event(
        request,
        "mission_confirmed",
        missionId=mission_id,
        result="granted" if result["reason"] is None else result["reason"],
        rewards=result["items"],
    )
    return {"items": result["items"]}


@router.post("/mission/autoConfirmMissions")
@player_data_decorator
async def mission_autoConfirmMissions(player_data, request: Request):
    request_json = await request.json()
    mission_type = request_json.get("type")
    if mission_type not in SUPPORTED_MISSION_TYPES:
        event(
            request,
            "missions_auto_confirmed",
            missionType=mission_type,
            result="unsupported_mission_type",
            claimedMissionIds=[],
            rewards=[],
        )
        return {"items": []}

    missions = player_data["mission"]["missions"][mission_type]
    pending = [mission_id for mission_id, _ in missions]
    claimed = []
    items = []
    skipped = {}

    # Revisit predecessor-blocked missions after an earlier mission in the
    # same request is claimed. No mission progress is advanced here.
    while pending:
        made_progress = False
        remaining = []
        for mission_id in pending:
            result = _claim_mission(player_data, mission_id, mission_type)
            if result["reason"] is None:
                made_progress = True
                claimed.append(mission_id)
                items.extend(result["items"])
            elif result["reason"] == "pre_mission_not_claimed":
                remaining.append(mission_id)
            else:
                skipped[mission_id] = result["reason"]
        if not made_progress:
            for mission_id in remaining:
                skipped[mission_id] = "pre_mission_not_claimed"
            break
        pending = remaining

    event(
        request,
        "missions_auto_confirmed",
        missionType=mission_type,
        result="granted" if claimed else "no_claimable_missions",
        claimedMissionIds=claimed,
        skippedReasons=skipped,
        rewards=items,
    )
    return {"items": items}
