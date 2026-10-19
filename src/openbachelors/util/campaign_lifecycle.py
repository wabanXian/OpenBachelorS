"""Resource-driven unlock lifecycle for permanent annihilation stages."""

from ..const.filepath import CAMPAIGN_TABLE, STAGE_TABLE
from .const_json_loader import const_json_loader
from .faketime import faketime


PASS_STATE = 2


def _active_row(rows, now):
    return next(
        (row for _, row in rows if row["startTs"] <= now <= row["endTs"]),
        None,
    )


def current_campaign_open_state(now=None):
    """Build both current-client fields and legacy aliases from GameData."""
    table = const_json_loader[CAMPAIGN_TABLE]
    ts = int(faketime() if now is None else now)
    permanent = _active_row(table["campaignGroups"], ts)
    rotate = _active_row(table["campaignRotateStageOpenTimes"], ts)
    training = _active_row(table["campaignTrainingStageOpenTimes"], ts)
    training_all = _active_row(table["campaignTrainingAllOpenTimes"], ts)
    return {
        "activeGroupId": permanent["groupId"] if permanent is not None else None,
        "permanent": (
            permanent["activeCamps"].copy() if permanent is not None else []
        ),
        "rotate": rotate["stageId"] if rotate is not None else None,
        "rotateGroup": rotate["groupId"] if rotate is not None else None,
        "training": training["stages"].copy() if training is not None else [],
        "trainingGroup": training["groupId"] if training is not None else None,
        "trainingAllOpenGroup": (
            training_all["groupId"] if training_all is not None else None
        ),
    }


def initial_campaign_instance(stage_id):
    campaign = const_json_loader[CAMPAIGN_TABLE]["campaigns"][stage_id]
    return {
        "maxKills": 0,
        "rewardStatus": [0] * len(campaign["breakLadders"]),
    }


def _condition_met(player_data, condition, future_stage_states=None):
    stage_id = condition["stageId"]
    required = 3 if condition["completeState"] == "COMPLETE" else PASS_STATE
    if future_stage_states and stage_id in future_stage_states:
        return future_stage_states[stage_id] >= required
    stages = player_data["dungeon"]["stages"]
    return stage_id in stages and stages[stage_id]["state"] >= required


def eligible_permanent_campaigns(player_data, future_stage_states=None):
    """Return permanent campaign IDs whose explicit stage conditions pass."""
    campaigns = const_json_loader[CAMPAIGN_TABLE]["campaigns"]
    stages = const_json_loader[STAGE_TABLE]["stages"]
    eligible = []
    for stage_id, _ in campaigns:
        if stage_id.startswith("camp_r_") or stage_id not in stages:
            continue
        conditions = stages[stage_id]["unlockCondition"].copy()
        if conditions and all(
            _condition_met(player_data, condition, future_stage_states)
            for condition in conditions
        ):
            eligible.append(stage_id)
    return eligible


def plan_campaign_unlocks(player_data, future_stage_states=None):
    """Build absolute mutations suitable for the battle settlement journal."""
    opened = set(player_data["campaignsV2"]["open"]["permanent"].copy())
    instances = player_data["campaignsV2"]["instances"]
    mutations, changes = [], []
    eligible = eligible_permanent_campaigns(player_data, future_stage_states)
    state = current_campaign_open_state() if eligible else None

    # ``open.permanent`` describes the complete resource-scheduled campaign
    # group.  Individual player access is still controlled by the stage table
    # and dungeon.stages.  Sending only the one currently accessible stage
    # leaves activeGroupId pointing at an incomplete group, so the client does
    # not construct the annihilation layout used by the 1-5 tutorial.
    scheduled = state["permanent"] if state is not None else []
    for stage_id in scheduled:
        if stage_id not in opened:
            opened.add(stage_id)
            changes.append({"stageId": stage_id, "kind": "permanent_opened"})
        if stage_id not in instances:
            mutations.append(
                {
                    "path": ["campaignsV2", "instances", stage_id],
                    "value": initial_campaign_instance(stage_id),
                }
            )
            changes.append({"stageId": stage_id, "kind": "instance_initialized"})
    # The current PC client builds the annihilation card on the common
    # "campaign" tab from the rotating stage, even before that stage itself is
    # unlocked. StageZoneCampaignViewModel.LoadData returns isValid=false when
    # open.rotate has no matching PlayerCampaign.instances entry. Keep this
    # zero-progress instance separate from dungeon.stages: it makes the card
    # structurally valid without unlocking or completing the rotating stage.
    rotate_stage_id = state["rotate"] if state is not None else None
    if rotate_stage_id is not None and rotate_stage_id not in instances:
        mutations.append(
            {
                "path": ["campaignsV2", "instances", rotate_stage_id],
                "value": initial_campaign_instance(rotate_stage_id),
            }
        )
        changes.append(
            {"stageId": rotate_stage_id, "kind": "rotate_instance_initialized"}
        )
    if changes:
        mutations.insert(
            0,
            {
                "path": ["campaignsV2", "open", "permanent"],
                "value": sorted(opened),
            },
        )
    if state is not None:
        campaign = player_data["campaignsV2"]
        active_present = "activeGroupId" in campaign
        current_active = campaign["activeGroupId"] if active_present else None
        if not active_present or current_active != state["activeGroupId"]:
            mutations.append(
                {
                    "path": ["campaignsV2", "activeGroupId"],
                    "value": state["activeGroupId"],
                }
            )
            changes.append(
                {
                    "kind": "active_group_synchronized",
                    "groupId": state["activeGroupId"],
                }
            )
        open_state = campaign["open"]
        aliases = {
            "rotate": state["rotate"],
            "rotateGroup": state["rotateGroup"],
            "rGroup": state["rotateGroup"],
            "training": state["training"],
            "trainingGroup": state["trainingGroup"],
            "tGroup": state["trainingGroup"],
            "trainingAllOpenGroup": state["trainingAllOpenGroup"],
            "tAllOpen": state["trainingAllOpenGroup"],
        }
        for field, value in aliases.items():
            present = field in open_state
            current = (
                open_state[field].copy()
                if present and field == "training"
                else (open_state[field] if present else None)
            )
            if present and current == value:
                continue
            mutations.append(
                {"path": ["campaignsV2", "open", field], "value": value}
            )
            changes.append({"kind": "open_field_synchronized", "field": field})
    return mutations, changes


def reconcile_campaign_unlocks(player_data):
    """Idempotently repair missing open/instance rows on existing saves."""
    mutations, changes = plan_campaign_unlocks(player_data)
    for mutation in mutations:
        target = player_data
        for key in mutation["path"][:-1]:
            target = target[key]
        target[mutation["path"][-1]] = mutation["value"]
    return changes
