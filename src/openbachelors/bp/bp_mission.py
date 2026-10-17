"""Resource-driven claiming for completed ordinary main-stage missions."""

from fastapi import APIRouter, Request

from ..const.filepath import MISSION_TABLE
from ..util.const_json_loader import const_json_loader
from ..util.player_data import player_data_decorator
from ..util.tutorial_debug import event
from ..util.reward_grant import apply_reward_immediate, reward_target_path


router = APIRouter()


def _mission_record(player_data, mission_id):
    for _, missions in player_data["mission"]["missions"]:
        if mission_id in missions:
            return missions[mission_id]
    return None


def _grant_mission_reward(player_data, mission_id):
    mission = const_json_loader[MISSION_TABLE]["missions"][mission_id]
    rewards = mission["rewards"].copy()
    unsupported = [item["type"] for item in rewards if reward_target_path(item) is None]
    if unsupported:
        raise RuntimeError(
            f"unsupported {mission_id} reward types {sorted(set(unsupported))}"
        )
    granted = []
    for reward in rewards:
        granted.append(apply_reward_immediate(player_data, reward))
    return granted


@router.post("/mission/confirmMission")
@player_data_decorator
async def mission_confirmMission(player_data, request: Request):
    request_json = await request.json()
    mission_id = request_json.get("missionId")
    reason = None
    items = []

    resources = const_json_loader[MISSION_TABLE]["missions"]
    resource = resources[mission_id] if mission_id in resources else None
    mission_type = resource["type"] if resource is not None else None
    missions = (
        player_data["mission"]["missions"][mission_type]
        if mission_type in {"MAIN", "SUB"}
        else None
    )
    if resource is None or missions is None or mission_id not in missions:
        reason = "unsupported_or_missing_mission"
    elif not (
        resource["template"] == "CompleteMainStage"
        or (resource["template"] == "CompleteStage" and mission_type == "SUB")
    ):
        reason = "unsupported_mission_template"
    else:
        mission = missions[mission_id]
        complete = all(
            progress["value"] >= progress["target"]
            for _, progress in mission["progress"]
        )
        if mission["state"] == 3:
            reason = "already_claimed"
        elif mission["state"] != 2 or not complete:
            reason = "mission_not_complete"
        elif any(
            (_mission_record(player_data, predecessor) is None)
            or _mission_record(player_data, predecessor)["state"] != 3
            for predecessor in (
                resource["preMissionIds"].copy()
                if resource["preMissionIds"] is not None
                else []
            )
        ):
            reason = "pre_mission_not_claimed"
        else:
            items = _grant_mission_reward(player_data, mission_id)
            mission["state"] = 3
            group_id = resource["missionGroup"]
            player_data["mission"]["missionGroups"][group_id] = 1

    event(
        request,
        "mission_confirmed",
        missionId=mission_id,
        result="granted" if items else reason,
        rewards=items,
    )
    return {"items": items}
