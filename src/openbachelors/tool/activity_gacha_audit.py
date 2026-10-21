"""Read-only evidence audit for historical activity / gacha-pool alignment.

An overlapping schedule is NOT a verified activity-to-banner relationship.
This module only emits evidence, never opens an event or changes an account.
"""

import argparse
import json
from collections import Counter
from pathlib import Path

from ..const.filepath import ACTIVITY_TABLE, GACHA_TABLE, STAGE_TABLE


def _timestamp(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    if isinstance(value, str) and value.isdecimal() and int(value) > 0:
        return int(value)
    return None


def _pool_ids_in_object(obj, known_ids):
    """Find IDs only inside explicitly pool-named fields, not arbitrary text."""
    result = set()

    def visit(value, field=""):
        if field in {"gachapoolid", "gachapoolids", "poolid", "poolids"}:
            if isinstance(value, str) and value in known_ids:
                result.add(value)
            elif isinstance(value, (list, tuple)):
                for member in value:
                    visit(member, field)
            elif isinstance(value, dict):
                for member in value.values():
                    visit(member, field)
            return
        if isinstance(value, dict):
            for key, member in value.items():
                visit(member, str(key).replace("_", "").lower())
        elif isinstance(value, list):
            for member in value:
                visit(member)

    visit(obj)
    return result


def _activity_runtime(table, activity_type, activity_id):
    types = table.get("activity") or {}
    if not isinstance(types, dict):
        return None
    group = types.get(activity_type)
    if not isinstance(group, dict):
        return None
    return group.get(activity_id)


def _zone_candidates(stage_table, activity_id):
    if not stage_table:
        return []
    stages = stage_table.get("stages") or {}
    if not isinstance(stages, dict):
        return []
    zones = {
        stage.get("zoneId")
        for stage in stages.values()
        if isinstance(stage, dict)
        and isinstance(stage.get("zoneId"), str)
        and (
            stage["zoneId"].startswith(activity_id + "_")
            or stage["zoneId"] == activity_id
        )
    }
    return sorted(zones)


def build_audit(activity_table, gacha_table, stage_table=None, *, candidate_limit=10):
    """Produce candidates with evidence labels, without mutating input objects."""
    basic = activity_table.get("basicInfo") or {}
    pools = gacha_table.get("gachaPoolClient") or []
    if not isinstance(basic, dict) or not isinstance(pools, list):
        raise ValueError("unsupported activity/gacha GameData structure")
    if candidate_limit < 0:
        raise ValueError("candidate_limit must be non-negative")

    known = {}
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        pool_id = pool.get("gachaPoolId")
        if not isinstance(pool_id, str) or not pool_id:
            continue
        if pool_id in known:
            raise ValueError(f"duplicate gacha pool ID: {pool_id}")
        known[pool_id] = pool

    rows = []
    for activity_id, info in sorted(basic.items()):
        if not isinstance(info, dict):
            continue
        kind = info.get("type")
        start = _timestamp(info.get("startTime"))
        end = _timestamp(info.get("endTime"))
        runtime = _activity_runtime(activity_table, kind, activity_id)
        explicit = _pool_ids_in_object(info, known)
        if runtime is not None:
            explicit.update(_pool_ids_in_object(runtime, known))

        overlap = []
        if start is not None and end is not None and start <= end:
            for pool_id, pool in known.items():
                p_start = _timestamp(pool.get("openTime"))
                p_end = _timestamp(pool.get("endTime"))
                if p_start is None or p_end is None or p_start > p_end:
                    continue
                seconds = min(end, p_end) - max(start, p_start) + 1
                if seconds > 0:
                    overlap.append(
                        {
                            "poolId": pool_id,
                            "poolName": pool.get("gachaPoolName"),
                            "ruleType": pool.get("gachaRuleType"),
                            "openTime": p_start,
                            "endTime": p_end,
                            "overlapSeconds": seconds,
                            "evidence": "time_overlap_only",
                        }
                    )
        overlap.sort(key=lambda item: (-item["overlapSeconds"], item["poolId"]))
        rows.append(
            {
                "activityId": activity_id,
                "activityType": kind,
                "activityName": info.get("name") or info.get("displayName"),
                "startTime": start,
                "endTime": end,
                "explicitPoolIds": sorted(explicit),
                "poolOverlapCount": len(overlap),
                "overlapCandidates": overlap[:candidate_limit],
                "zonePrefixCandidates": _zone_candidates(stage_table, activity_id),
                "status": (
                    "explicit_pool_id_in_activity_data"
                    if explicit
                    else "candidates_only_not_verified"
                    if overlap
                    else "no_pool_evidence"
                ),
            }
        )

    statuses = Counter(row["status"] for row in rows)
    return {
        "schemaVersion": 1,
        "purpose": "read_only_historical_activity_and_gacha_audit",
        "activityCount": len(rows),
        "poolCount": len(known),
        "statusCounts": dict(sorted(statuses.items())),
        "linkRule": "Explicit pool-ID reference is evidence. Time/zone overlap is only a candidate; never auto-open a pool from this report.",
        "accountChanges": False,
        "activities": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", default="data/activity_gacha_audit.json",
        help="Output path for read-only audit results",
    )
    parser.add_argument("--candidate-limit", type=int, default=10)
    args = parser.parse_args()
    # Read current checked-out resources. Do not use current server time.
    activity = json.loads(Path(ACTIVITY_TABLE).read_text(encoding="utf-8"))
    gacha = json.loads(Path(GACHA_TABLE).read_text(encoding="utf-8"))
    stages = json.loads(Path(STAGE_TABLE).read_text(encoding="utf-8"))
    report = build_audit(
        activity, gacha, stages, candidate_limit=args.candidate_limit
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "activityCount": report["activityCount"],
                "poolCount": report["poolCount"],
                "statusCounts": report["statusCounts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
