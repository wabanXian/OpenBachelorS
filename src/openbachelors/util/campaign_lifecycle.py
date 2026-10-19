"""Resource-driven unlock lifecycle for permanent annihilation stages."""

from ..const.filepath import CAMPAIGN_TABLE, STAGE_TABLE
from .const_json_loader import const_json_loader


PASS_STATE = 2


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
    for stage_id in eligible_permanent_campaigns(player_data, future_stage_states):
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
    if changes:
        mutations.insert(
            0,
            {
                "path": ["campaignsV2", "open", "permanent"],
                "value": sorted(opened),
            },
        )
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
