"""Build auditable ordinary-stage settlement rules from local resources."""

from collections import defaultdict
from copy import deepcopy
import re


SUPPORTED_STAGE_TYPES = {"MAIN", "SUB"}
SUPPORTED_DIFFICULTY = "NORMAL"
SUPPORTED_PERFORMANCE_FLAG = "NORMAL_STAGE"
MIN_MATRIX_SAMPLES = 100


def _mission_rules_by_stage(mission_table, valid_stage_ids):
    missions_by_stage = defaultdict(list)
    for mission_id, mission in mission_table["missions"].items():
        params = mission.get("param") or []
        supported = mission.get("template") == "CompleteMainStage" or (
            mission.get("template") == "CompleteStage"
            and mission.get("type") == "SUB"
        )
        if (
            not supported
            or len(params) < 2
            or params[1] not in valid_stage_ids
            or not str(params[-1]).isdigit()
        ):
            continue
        missions_by_stage[params[1]].append(
            {
                "id": mission_id,
                "type": mission["type"],
                "template": mission["template"],
                "missionGroup": mission["missionGroup"],
                "preMissionIds": deepcopy(mission.get("preMissionIds") or []),
                "target": int(params[-1]),
                "rewards": deepcopy(mission.get("rewards") or []),
            }
        )
    return missions_by_stage


def is_ordinary_stage(stage_id, stage):
    """Conservative boundary for the protocol currently verified as ordinary."""
    return (
        (stage_id.startswith("main_") or stage_id.startswith("sub_"))
        and "#" not in stage_id
        and str(stage.get("zoneId", "")).startswith("main_")
        and stage.get("stageType") in SUPPORTED_STAGE_TYPES
        and stage.get("difficulty") == SUPPORTED_DIFFICULTY
        and stage.get("performanceStageFlag") == SUPPORTED_PERFORMANCE_FLAG
        and not stage.get("isStoryOnly")
        and not stage.get("isPredefined")
    )


def _current_observations(dataset_stage):
    """Choose one current/best matrix interval per item without summing eras."""
    selected = {}
    for observation in (dataset_stage.get("penguin") or {}).get(
        "matrix_observations", []
    ):
        item_id = observation.get("item_id")
        if not item_id or item_id in {"furni"} or item_id.startswith("randomMaterial_"):
            continue
        current = selected.get(item_id)
        score = (
            observation.get("end") is None,
            observation.get("times") or 0,
            observation.get("start") or 0,
        )
        current_score = (
            current.get("end") is None,
            current.get("times") or 0,
            current.get("start") or 0,
        ) if current else None
        if current is None or score > current_score:
            selected[item_id] = observation
    return selected


def _drop_rule(item_id, observation, item_table):
    quantity = observation.get("quantity")
    times = observation.get("times")
    if not isinstance(quantity, int) or not isinstance(times, int) or times < MIN_MATRIX_SAMPLES:
        return None, "missing_or_low_sample_matrix"
    infos = observation.get("drop_infos") or []
    supported_infos = [
        info
        for info in infos
        if info.get("drop_type") in {"NORMAL_DROP", "EXTRA_DROP"}
    ]
    if not supported_infos:
        return None, "unclassified_or_special_matrix_drop"
    item = item_table.get(item_id)
    if not item:
        return None, "unknown_item_id"
    classes = []
    for info in supported_infos:
        bounds = info.get("bounds") or {}
        classes.append(
            {
                "responseField": (
                    "rewards"
                    if info.get("drop_type") == "NORMAL_DROP"
                    else "additionalRewards"
                ),
                "penguinDropType": info.get("drop_type"),
                "lower": bounds.get("lower"),
                "upper": bounds.get("upper"),
            }
        )
    max_upper = max(
        (entry["upper"] for entry in classes if isinstance(entry["upper"], int)),
        default=None,
    )
    algorithm = (
        "deterministic_bernoulli_from_marginal_mean"
        if max_upper == 1 and quantity <= times
        else "deterministic_stochastic_round_marginal_mean"
    )
    return (
        {
            "type": item.get("itemType") or "MATERIAL",
            "id": item_id,
            "quantity": quantity,
            "times": times,
            "mean": quantity / times,
            "start": observation.get("start"),
            "end": observation.get("end"),
            "classes": classes,
            "algorithm": algorithm,
            "source": "Penguin Statistics CN matrix exact stageId/itemId match",
            "license": "CC BY-NC 4.0",
            "warning": (
                "Marginal single-player approximation; not an official per-run "
                "distribution and does not model mutual exclusion"
            ),
        },
        None,
    )


def build_settlement_rules(stage_table, mission_table, drop_dataset, manual_rules):
    missions_by_stage = _mission_rules_by_stage(
        mission_table, set(stage_table["stages"])
    )

    result = {}
    report = {
        "ordinaryStageCount": 0,
        "settlementEnabledCount": 0,
        "completeRuleCount": 0,
        "approximateDropStageCount": 0,
        "deterministicOnlyStageCount": 0,
        "stagesWithUnconfirmedFirstPassRewards": {},
        "stagesWithoutExactMatrix": [],
        "lowSampleOrUnusableDrops": {},
        "chapterCoverage": {},
    }
    dataset_stages = drop_dataset["stages"]
    item_table = drop_dataset["items"]
    manual_stage_rules = manual_rules.get("stages", {})

    for stage_id, stage in stage_table["stages"].items():
        if not is_ordinary_stage(stage_id, stage):
            continue
        report["ordinaryStageCount"] += 1
        zone_id = stage["zoneId"]
        chapter = report["chapterCoverage"].setdefault(
            zone_id, {"ordinary": 0, "enabled": 0, "complete": 0, "approximate": 0}
        )
        chapter["ordinary"] += 1
        dataset_stage = dataset_stages.get(stage_id) or {}
        exact_matrix = bool(
            (dataset_stage.get("penguin") or {}).get(
                "stage_metadata_matched_by_exact_id"
            )
        ) and not (dataset_stage.get("validation") or {}).get(
            "resource_version_mismatches"
        )
        drop_rules = []
        unusable = []
        if exact_matrix:
            for item_id, observation in _current_observations(dataset_stage).items():
                rule, reason = _drop_rule(item_id, observation, item_table)
                if rule:
                    drop_rules.append(rule)
                elif reason:
                    unusable.append({"itemId": item_id, "reason": reason})
        else:
            report["stagesWithoutExactMatrix"].append(stage_id)

        display_once = [
            {
                "type": reward.get("type"),
                "id": reward.get("id"),
                "count": reward.get("count"),
                "evidence": "stage_table.displayDetailRewards ONCE",
            }
            for reward in ((stage.get("stageDropInfo") or {}).get("displayDetailRewards") or [])
            if reward.get("dropType") == "ONCE"
        ]
        confirmed_first = deepcopy(
            (manual_stage_rules.get(stage_id) or {}).get("firstPass") or []
        )
        confirmed_keys = {(item["type"], item["id"]) for item in confirmed_first}
        unresolved_first = [
            item
            for item in display_once
            if (item["type"], item["id"]) not in confirmed_keys
            and not isinstance(item.get("count"), int)
        ]
        confirmed_first.extend(
            item
            for item in display_once
            if isinstance(item.get("count"), int)
            and (item["type"], item["id"]) not in confirmed_keys
        )
        enabled = not unresolved_first
        complete = enabled and exact_matrix
        if enabled:
            report["settlementEnabledCount"] += 1
            chapter["enabled"] += 1
        if complete:
            report["completeRuleCount"] += 1
            chapter["complete"] += 1
        if drop_rules:
            report["approximateDropStageCount"] += 1
            chapter["approximate"] += 1
        else:
            report["deterministicOnlyStageCount"] += 1
        if unresolved_first:
            report["stagesWithUnconfirmedFirstPassRewards"][stage_id] = unresolved_first
        if unusable:
            report["lowSampleOrUnusableDrops"][stage_id] = unusable

        result[stage_id] = {
            "stageId": stage_id,
            "stageType": stage["stageType"],
            "zoneId": zone_id,
            "difficulty": stage["difficulty"],
            "settlementEnabled": enabled,
            "ruleCompleteness": "complete" if complete else "partial",
            "apCost": stage["apCost"],
            "apFailReturn": stage["apFailReturn"],
            "expGain": stage["expGain"],
            "goldGain": stage["goldGain"],
            "diamondOnceDrop": stage["diamondOnceDrop"],
            "unlockCondition": deepcopy(stage.get("unlockCondition") or []),
            "firstPassRewards": confirmed_first,
            "unconfirmedFirstPassRewards": unresolved_first,
            "dropRules": drop_rules,
            "missions": sorted(missions_by_stage.get(stage_id, []), key=lambda item: item["id"]),
            "sources": {
                "deterministic": "res/excel/stage_table.json and mission_table.json",
                "random": "drop_data/stage_drop_dataset_cn.json",
            },
        }

    return {
        "schemaVersion": 1,
        "runtimeStatus": "ordinary_mainline_settlement_rules",
        "selection": {
            "stageIdPrefixes": ["main_", "sub_"],
            "zonePrefix": "main_",
            "stageTypes": sorted(SUPPORTED_STAGE_TYPES),
            "difficulty": SUPPORTED_DIFFICULTY,
            "performanceStageFlag": SUPPORTED_PERFORMANCE_FLAG,
            "excludeStoryOnly": True,
            "excludePredefined": True,
            "excludeVariantIdsContainingHash": True,
        },
        "license": deepcopy(drop_dataset.get("license")),
        "stages": result,
        "report": report,
    }


def build_training_settlement_rules(stage_table, mission_table):
    """Generate only normal numbered TR stages in main zones.

    CHAR ONCE+ALWAYS is interpreted as one acquisition. That protocol shape is
    established by real-client TR-1/TR-2 samples; other reward quantities are
    never inferred from display rows.
    """
    stages = stage_table["stages"]
    missions_by_stage = _mission_rules_by_stage(mission_table, set(stages))
    normal_ids = sorted(
        stage_id
        for stage_id, stage in stages.items()
        if re.fullmatch(r"tr_\d+", stage_id)
        and str(stage.get("zoneId", "")).startswith("main_")
        and stage.get("isPredefined") is True
    )
    special_main = sorted(
        stage_id
        for stage_id, stage in stages.items()
        if stage_id.startswith("tr_s_")
        and str(stage.get("zoneId", "")).startswith("main_")
    )
    activity_numbered = sorted(
        stage_id
        for stage_id, stage in stages.items()
        if re.fullmatch(r"tr_\d+", stage_id)
        and not str(stage.get("zoneId", "")).startswith("main_")
    )
    other_training = sorted(
        stage_id
        for stage_id, stage in stages.items()
        if stage_id not in normal_ids + special_main + activity_numbered
        and (
            stage_id.startswith("lt_tr_")
            or "training" in str(stage.get("levelId", "")).lower()
            or "tutorial" in str(stage.get("levelId", "")).lower()
        )
    )

    generated = {}
    unsupported = {}
    for stage_id in normal_ids:
        stage = stages[stage_id]
        display = (
            (stage.get("stageDropInfo") or {}).get("displayDetailRewards") or []
        )
        first_pass = []
        unresolved = []
        for reward in display:
            if reward.get("dropType") != "ONCE":
                continue
            if reward.get("type") == "CHAR" and reward.get("occPercent") == "ALWAYS":
                first_pass.append(
                    {
                        "type": "CHAR",
                        "id": reward["id"],
                        "count": 1,
                        "evidence": (
                            "stage_table ONCE+ALWAYS CHAR; quantity/charGet semantics "
                            "generalized from real-client TR-1/TR-2"
                        ),
                    }
                )
            elif isinstance(reward.get("count"), int):
                first_pass.append(
                    {
                        "type": reward["type"],
                        "id": reward["id"],
                        "count": reward["count"],
                        "evidence": "stage_table counted ONCE reward",
                    }
                )
            else:
                unresolved.append(deepcopy(reward))
        enabled = not unresolved
        if unresolved:
            unsupported[stage_id] = {
                "reason": "unconfirmed_first_pass_reward",
                "rewards": unresolved,
            }
        generated[stage_id] = {
            "stageId": stage_id,
            "stageType": stage["stageType"],
            "zoneId": stage["zoneId"],
            "difficulty": stage["difficulty"],
            "settlementEnabled": enabled,
            "ruleCompleteness": "generated_training" if enabled else "partial",
            "apCost": stage["apCost"],
            "apFailReturn": stage["apFailReturn"],
            "expGain": stage["expGain"],
            "goldGain": stage["goldGain"],
            "diamondOnceDrop": stage["diamondOnceDrop"],
            "unlockCondition": deepcopy(stage.get("unlockCondition") or []),
            "firstPassRewards": first_pass,
            "unconfirmedFirstPassRewards": unresolved,
            "dropRules": [],
            "missions": sorted(
                missions_by_stage.get(stage_id, []), key=lambda item: item["id"]
            ),
            "sources": {
                "deterministic": "res/excel/stage_table.json and mission_table.json",
                "protocolBaseline": "real-client TR-1/TR-2",
                "random": None,
            },
        }

    task_map = {
        stage_id: deepcopy(generated[stage_id]["missions"])
        for stage_id in normal_ids
    }
    reward_map = {
        stage_id: generated[stage_id]["firstPassRewards"]
        for stage_id in normal_ids
    }
    stage_coverage = {}
    for stage_id in normal_ids:
        stage_coverage[stage_id] = {
            "status": (
                "supported"
                if generated[stage_id]["settlementEnabled"]
                else "unsupported"
            ),
            "apCost": generated[stage_id]["apCost"],
            "apFailReturn": generated[stage_id]["apFailReturn"],
            "expGain": generated[stage_id]["expGain"],
            "goldGain": generated[stage_id]["goldGain"],
            "diamondOnceDrop": generated[stage_id]["diamondOnceDrop"],
            "firstPassRewards": deepcopy(generated[stage_id]["firstPassRewards"]),
            "missions": deepcopy(generated[stage_id]["missions"]),
            "unlockCondition": deepcopy(generated[stage_id]["unlockCondition"]),
            "unlockCandidates": sorted(
                candidate_id
                for candidate_id, candidate in stages.items()
                if any(
                    condition.get("stageId") == stage_id
                    for condition in (candidate.get("unlockCondition") or [])
                )
            ),
        }
    return {
        "schemaVersion": 1,
        "runtimeStatus": "mainline_training_settlement_rules",
        "selection": {
            "stageIdPattern": "^tr_[0-9]+$",
            "zonePrefix": "main_",
            "requirePredefined": True,
        },
        "stages": generated,
        "report": {
            "mainlineTrainingCount": len(normal_ids),
            "supportedCount": sum(item["settlementEnabled"] for item in generated.values()),
            "ids": normal_ids,
            "unsupported": unsupported,
            "specialMainTrainingExcluded": special_main,
            "activityNumberedTrainingExcluded": activity_numbered,
            "otherTrainingExcluded": other_training,
            "taskMap": task_map,
            "firstPassRewardMap": reward_map,
            "stageCoverage": stage_coverage,
        },
    }
