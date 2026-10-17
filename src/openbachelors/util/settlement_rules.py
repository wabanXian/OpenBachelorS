"""Runtime access to generated ordinary rules and audited special overrides."""

from copy import deepcopy

from ..const.filepath import (
    GENERATED_STAGE_SETTLEMENT_RULES,
    GENERATED_TRAINING_SETTLEMENT_RULES,
    MISSION_TABLE,
    STAGE_SETTLEMENT_RULES,
    STAGE_TABLE,
)
from .const_json_loader import const_json_loader


def _missions_for_stage(stage_id):
    result = []
    for mission_id, mission in const_json_loader[MISSION_TABLE]["missions"]:
        params = mission["param"].copy()
        supported = mission["template"] == "CompleteMainStage" or (
            mission["template"] == "CompleteStage" and mission["type"] == "SUB"
        )
        if not supported or len(params) < 2:
            continue
        if params[1] != stage_id:
            continue
        result.append(
            {
                "id": mission_id,
                "type": mission["type"],
                "missionGroup": mission["missionGroup"],
                "target": int(params[-1]),
                "rewards": mission["rewards"].copy(),
            }
        )
    return sorted(result, key=lambda item: item["id"])


def get_stage_rule(stage_id):
    generated = const_json_loader[GENERATED_STAGE_SETTLEMENT_RULES]["stages"]
    if stage_id in generated:
        rule = generated[stage_id].copy()
        if not rule["settlementEnabled"]:
            return None, "unconfirmed_first_pass_reward"
        return rule, "generated_ordinary_rule"

    training = const_json_loader[GENERATED_TRAINING_SETTLEMENT_RULES]["stages"]
    if stage_id in training:
        rule = training[stage_id].copy()
        if not rule["settlementEnabled"]:
            return None, "unconfirmed_training_first_pass_reward"
        return rule, "generated_training_rule"

    manual = const_json_loader[STAGE_SETTLEMENT_RULES]["stages"]
    if stage_id not in manual:
        return None, "stage_settlement_not_audited"
    stage = const_json_loader[STAGE_TABLE]["stages"][stage_id]
    special = manual[stage_id].copy()
    return (
        {
            "stageId": stage_id,
            "stageType": stage["stageType"],
            "zoneId": stage["zoneId"],
            "difficulty": stage["difficulty"],
            "settlementEnabled": True,
            "ruleCompleteness": "audited_special",
            "apCost": stage["apCost"],
            "apFailReturn": stage["apFailReturn"],
            "expGain": stage["expGain"],
            "goldGain": stage["goldGain"],
            "diamondOnceDrop": stage["diamondOnceDrop"],
            "unlockCondition": stage["unlockCondition"].copy(),
            "firstPassRewards": deepcopy(special.get("firstPass") or []),
            "unconfirmedFirstPassRewards": [],
            "dropRules": [],
            "missions": _missions_for_stage(stage_id),
            "sources": {
                "deterministic": "stage_table plus audited special override",
                "random": None,
            },
        },
        "audited_special_rule",
    )


def generated_rule_report():
    return const_json_loader[GENERATED_STAGE_SETTLEMENT_RULES]["report"].copy()
