"""Generate a read-only audit of mission visibility and progress support."""

import argparse
import json
from collections import Counter
from pathlib import Path

from ..const.filepath import MISSION_LIFECYCLE_AUDIT_REPORT, MISSION_TABLE
from ..util.const_json_loader import const_json_loader
from ..util.mission_lifecycle import VISIBLE_TYPES


PROGRESS_TEMPLATES = {
    "CompleteMainStage": "battle_settlement",
    "CompleteStage": "battle_settlement",
    "UpgradeChar": "charBuild/upgradeChar",
    "UpgradePlayer": "player_progression",
}


def build_audit():
    table = const_json_loader[MISSION_TABLE]
    rows = []
    for mission_id, mission in table["missions"]:
        mission_type = mission["type"]
        template = mission["template"]
        visible = mission_type in VISIBLE_TYPES
        rows.append(
            {
                "missionId": mission_id,
                "type": mission_type,
                "template": template,
                "missionGroup": mission["missionGroup"],
                "preMissionIds": (
                    mission["preMissionIds"].copy()
                    if mission["preMissionIds"] is not None
                    else []
                ),
                "unlockCondition": mission["unlockCondition"],
                "visibilitySupport": "implemented" if visible else "not_initialized",
                "progressSupport": PROGRESS_TEMPLATES.get(template, "not_implemented"),
                "claimSupport": "implemented_when_record_is_complete" if visible else "not_implemented",
            }
        )
    type_counts = Counter(row["type"] for row in rows)
    progress_counts = Counter(row["progressSupport"] for row in rows)
    return {
        "schemaVersion": 1,
        "missionCount": len(rows),
        "typeCounts": dict(sorted(type_counts.items())),
        "visibilityImplementedTypes": sorted(VISIBLE_TYPES),
        "progressSupportCounts": dict(sorted(progress_counts.items())),
        "groupRewardClaim": "not_implemented_protocol_unverified",
        "periodicLifecycle": {
            "DAILY": "not_initialized_reset_window_and_point_protocol_unverified",
            "WEEKLY": "not_initialized_reset_window_and_point_protocol_unverified",
            "SPECIAL_OPERATOR_WEEKLY": "not_initialized",
        },
        "missions": rows,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=MISSION_LIFECYCLE_AUDIT_REPORT)
    args = parser.parse_args()
    report = build_audit()
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "missionCount": report["missionCount"],
                "typeCounts": report["typeCounts"],
                "progressSupportCounts": report["progressSupportCounts"],
                "periodicLifecycle": report["periodicLifecycle"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
