"""Audit generated ordinary settlement rules against independent local evidence."""

import argparse
import json
from collections import Counter
from pathlib import Path

from ..const.filepath import (
    GENERATED_STAGE_SETTLEMENT_RULES,
    STAGE_SETTLEMENT_AUDIT_REPORT,
    STAGE_SETTLEMENT_RULES,
    STAGE_TABLE,
)


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _category(stage_id, stage):
    code = str(stage.get("code") or "").upper()
    if stage_id.startswith("tr_"):
        return "main_training"
    if "-TR-" in code or stage_id.endswith("_tr"):
        return "event_training"
    if stage.get("isStoryOnly") or stage.get("stageType") == "STORY":
        return "story"
    if stage.get("difficulty") != "NORMAL":
        return "special_difficulty"
    if stage.get("performanceStageFlag") != "NORMAL_STAGE":
        return "predefined_or_performance"
    if stage.get("stageType") in {"MAIN", "SUB"}:
        return "mainline_outside_ordinary_selection"
    if str(stage.get("zoneId") or "").startswith("weekly_"):
        return "annihilation"
    if str(stage.get("zoneId") or "").startswith("main_"):
        return "mainline_other"
    return "resource_or_event"


def build_audit():
    stage_table = _load(STAGE_TABLE)["stages"]
    generated = _load(GENERATED_STAGE_SETTLEMENT_RULES)
    overrides = _load(STAGE_SETTLEMENT_RULES).get("stages", {})
    rows = []
    errors = []
    for stage_id, rule in generated["stages"].items():
        stage = stage_table[stage_id]
        local_once = [
            {"type": item.get("type"), "id": item.get("id")}
            for item in ((stage.get("stageDropInfo") or {}).get("displayDetailRewards") or [])
            if item.get("dropType") == "ONCE"
        ]
        first = rule["firstPassRewards"]
        first_keys = {(item["type"], item["id"]) for item in first}
        missing = [item for item in local_once if (item["type"], item["id"]) not in first_keys]
        bad_counts = [item for item in first if not isinstance(item.get("count"), int) or item["count"] <= 0]
        mismatch = {
            key: {"stage": stage.get(key), "rule": rule.get(key)}
            for key in ("apCost", "apFailReturn", "expGain", "goldGain", "diamondOnceDrop")
            if stage.get(key) != rule.get(key)
        }
        issues = []
        if not rule["settlementEnabled"]:
            issues.append("settlement_disabled")
        if missing:
            issues.append("missing_once_reward")
        if bad_counts:
            issues.append("invalid_first_pass_count")
        if mismatch:
            issues.append("stage_scalar_mismatch")
        if issues:
            errors.append({"stageId": stage_id, "issues": issues})
        manual = overrides.get(stage_id, {}).get("firstPass", [])
        rows.append(
            {
                "stageId": stage_id,
                "code": stage.get("code"),
                "zoneId": stage.get("zoneId"),
                "stageType": stage.get("stageType"),
                "status": "verified" if not issues else "unconfirmed",
                "deterministic": {
                    "apCost": rule["apCost"],
                    "apFailReturn": rule["apFailReturn"],
                    "expGain": rule["expGain"],
                    "goldGain": rule["goldGain"],
                    "diamondOnceDrop": rule["diamondOnceDrop"],
                    "unlockCondition": rule["unlockCondition"],
                    "missions": [item["id"] for item in rule["missions"]],
                    "firstPassRewards": first,
                },
                "randomDrops": {
                    "status": "approximate" if rule["dropRules"] else "no_exact_matrix",
                    "ruleCount": len(rule["dropRules"]),
                    "source": rule["sources"]["random"],
                },
                "independentChecks": {
                    "localOnceRewards": local_once,
                    "manualEvidence": manual,
                    "missing": missing,
                    "invalidCounts": bad_counts,
                    "scalarMismatch": mismatch,
                },
            }
        )
    excluded = Counter()
    excluded_mainline = []
    included = set(generated["stages"])
    for stage_id, stage in stage_table.items():
        if stage_id in included:
            continue
        category = _category(stage_id, stage)
        excluded[category] += 1
        if category in {"main_training", "story", "predefined_or_performance", "mainline_outside_ordinary_selection", "mainline_other"}:
            excluded_mainline.append(
                {
                    "stageId": stage_id,
                    "code": stage.get("code"),
                    "category": category,
                    "stageType": stage.get("stageType"),
                    "performanceStageFlag": stage.get("performanceStageFlag"),
                }
            )
    return {
        "schemaVersion": 1,
        "ordinaryStageCount": len(rows),
        "settlementEnabledCount": sum(row["status"] == "verified" for row in rows),
        "auditErrorCount": len(errors),
        "errors": errors,
        "ordinaryStages": rows,
        "excludedStageSummary": dict(sorted(excluded.items())),
        "excludedMainlineRelevant": sorted(excluded_mainline, key=lambda item: item["stageId"]),
        "notes": [
            "Deterministic fields are checked against local stage_table; manual first-pass quantities carry source URLs in stage_settlement_rules.json.",
            "Penguin quantity/times drive a labeled marginal approximation and are not official per-run probabilities.",
            "Excluded stages are classification output only; this audit never enables or settles them.",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=STAGE_SETTLEMENT_AUDIT_REPORT)
    args = parser.parse_args()
    report = build_audit()
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: report[key] for key in ("ordinaryStageCount", "settlementEnabledCount", "auditErrorCount", "excludedStageSummary")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
