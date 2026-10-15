import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import requests

from ..const.filepath import STAGE_TABLE


API_BASE = "https://penguin-stats.io/PenguinStats/api/v2"
SOURCE_URLS = {
    "stages": f"{API_BASE}/stages?server=CN",
    "matrix": f"{API_BASE}/result/matrix?server=CN&show_closed_zones=true",
    "items": f"{API_BASE}/items?server=CN",
}
ATTRIBUTION = "Drop-rate statistics © Penguin Statistics, CC BY-NC 4.0."
LOW_SAMPLE_DEFAULT = 100


def _sha256_bytes(data):
    return sha256(data).hexdigest()


def _write_json(path, value, pretty=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(
        value,
        ensure_ascii=False,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    )
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content + "\n", encoding="utf-8")
    temporary.replace(path)


def _download_json(url, timeout):
    session = requests.Session()
    session.trust_env = False
    response = session.get(
        url,
        timeout=timeout,
        headers={"User-Agent": "OpenBachelorS-stage-drop-audit/1.0"},
    )
    response.raise_for_status()
    return response.content, response.json()


def acquire_sources(output_dir, source_dir=None, timeout=90):
    raw_dir = output_dir / "raw"
    acquired = {}
    errors = []
    cached_manifest = {}
    if source_dir is not None:
        manifest_path = source_dir / "source_manifest.json"
        if manifest_path.exists():
            cached_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, url in SOURCE_URLS.items():
        try:
            if source_dir is None:
                raw, value = _download_json(url, timeout)
                raw_dir.mkdir(parents=True, exist_ok=True)
                (raw_dir / f"penguin_{name}_cn.json").write_bytes(raw)
                mode = "network"
                retrieved_at = datetime.now(timezone.utc).isoformat()
            else:
                source_path = source_dir / f"penguin_{name}_cn.json"
                raw = source_path.read_bytes()
                value = json.loads(raw)
                mode = "local_cache"
                retrieved_at = (cached_manifest.get(name) or {}).get("retrieved_at")
                if retrieved_at is None:
                    retrieved_at = datetime.fromtimestamp(
                        source_path.stat().st_mtime, timezone.utc
                    ).isoformat()
            acquired[name] = {
                "value": value,
                "metadata": {
                    "url": url,
                    "mode": mode,
                    "retrieved_at": retrieved_at,
                    "sha256": _sha256_bytes(raw),
                    "bytes": len(raw),
                },
            }
        except Exception as exc:
            errors.append(
                {
                    "source": name,
                    "url": url,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
    if source_dir is None and acquired:
        _write_json(
            raw_dir / "source_manifest.json",
            {name: value["metadata"] for name, value in acquired.items()},
            pretty=True,
        )
    return acquired, errors


def _category(stage):
    stage_type = stage.get("stageType")
    if stage_type in {"MAIN", "SUB"}:
        return "mainline"
    if stage_type == "DAILY":
        return "resource"
    if stage_type == "ACTIVITY":
        return "activity"
    return "other"


def _local_drop_groups(stage):
    result = defaultdict(list)
    detail = (stage.get("stageDropInfo") or {}).get("displayDetailRewards") or []
    for drop in detail:
        result[drop.get("dropType", "UNKNOWN")].append(
            {
                "item_id": drop.get("id"),
                "item_type": drop.get("type"),
                "occurrence_label": drop.get("occPercent"),
                "count": drop.get("count"),
            }
        )
    return dict(sorted(result.items()))


def _drop_info_index(penguin_stage):
    result = defaultdict(list)
    for info in (penguin_stage or {}).get("dropInfos") or []:
        result[str(info.get("itemId"))].append(
            {
                "drop_type": info.get("dropType"),
                "bounds": info.get("bounds"),
            }
        )
    return result


def _observations(rows, penguin_stage):
    info_by_item = _drop_info_index(penguin_stage)
    observations = []
    for row in sorted(rows, key=lambda item: (str(item.get("itemId")), item.get("start") or 0)):
        item_id = str(row.get("itemId"))
        infos = info_by_item.get(item_id, [])
        bounds = [info.get("bounds") or {} for info in infos]
        lower_bounds = [bound.get("lower") for bound in bounds if bound.get("lower") is not None]
        upper_bounds = [bound.get("upper") for bound in bounds if bound.get("upper") is not None]
        quantity = row.get("quantity")
        times = row.get("times")
        multi_possible = any(value > 1 for value in upper_bounds)
        if isinstance(quantity, int) and isinstance(times, int) and quantity > times:
            multi_possible = True
        if lower_bounds:
            guarantee = "guaranteed" if min(lower_bounds) > 0 else "not_guaranteed"
        else:
            guarantee = "unknown"
        paired_bounds = [
            (bound.get("lower"), bound.get("upper"))
            for bound in bounds
            if bound.get("lower") is not None and bound.get("upper") is not None
        ]
        if paired_bounds and all(lower == upper and lower > 0 for lower, upper in paired_bounds):
            quantity_rule = "fixed_quantity"
        elif paired_bounds and all(lower > 0 for lower, _ in paired_bounds):
            quantity_rule = "guaranteed_quantity_range"
        elif paired_bounds and any(lower == 0 for lower, _ in paired_bounds):
            quantity_rule = "probabilistic_or_zero_quantity"
        else:
            quantity_rule = "unknown"
        observations.append(
            {
                "item_id": item_id,
                "quantity": quantity,
                "times": times,
                "start": row.get("start"),
                "end": row.get("end"),
                "std_dev": row.get("stdDev"),
                "drop_infos": infos,
                "guarantee_from_bounds": guarantee,
                "quantity_rule_from_bounds": quantity_rule,
                "multi_quantity_possible": multi_possible,
                "ratio_semantics": "mean_item_quantity_per_run_not_probability",
                "mutual_exclusion": "not_determinable_from_marginal_statistics",
            }
        )
    return observations


def build_dataset(local_table, penguin_stages, matrix, items, source_metadata, fetched_at, low_sample):
    local_stages = local_table["stages"]
    penguin_by_id = {str(stage["stageId"]): stage for stage in penguin_stages}
    matrix_by_stage = defaultdict(list)
    for row in matrix:
        matrix_by_stage[str(row["stageId"])].append(row)
    item_by_id = {str(item["itemId"]): item for item in items}

    coverage = defaultdict(lambda: {"local": 0, "metadata": 0, "matrix": 0})
    stage_type_coverage = defaultdict(lambda: {"local": 0, "metadata": 0, "matrix": 0})
    missing_metadata = []
    missing_matrix = []
    low_sample_stages = []
    version_mismatches = []
    multiple_quantity = []
    matched_without_drop_rules = []
    drop_type_counts = Counter()
    output_stages = {}

    for stage_id, local in sorted(local_stages.items()):
        category = _category(local)
        stage_type = local.get("stageType", "UNKNOWN")
        coverage[category]["local"] += 1
        stage_type_coverage[stage_type]["local"] += 1
        penguin = penguin_by_id.get(stage_id)
        rows = matrix_by_stage.get(stage_id, [])
        if penguin is not None:
            coverage[category]["metadata"] += 1
            stage_type_coverage[stage_type]["metadata"] += 1
        else:
            missing_metadata.append(stage_id)
        if rows:
            coverage[category]["matrix"] += 1
            stage_type_coverage[stage_type]["matrix"] += 1
        else:
            missing_matrix.append(stage_id)

        mismatches = []
        if penguin is not None:
            for local_key, penguin_key in (("code", "code"), ("zoneId", "zoneId"), ("apCost", "apCost")):
                if local.get(local_key) != penguin.get(penguin_key):
                    mismatches.append(
                        {
                            "field": local_key,
                            "local": local.get(local_key),
                            "penguin": penguin.get(penguin_key),
                        }
                    )
        if mismatches:
            version_mismatches.append({"stage_id": stage_id, "fields": mismatches})

        observations = _observations(rows, penguin)
        if penguin is not None and not penguin.get("dropInfos"):
            matched_without_drop_rules.append(stage_id)
        for info in (penguin or {}).get("dropInfos") or []:
            drop_type_counts[info.get("dropType", "UNKNOWN")] += 1
        if any(row["multi_quantity_possible"] for row in observations):
            multiple_quantity.append(stage_id)
        sample_values = [row.get("times") for row in rows if isinstance(row.get("times"), int)]
        max_samples = max(sample_values, default=0)
        if rows and max_samples < low_sample:
            low_sample_stages.append({"stage_id": stage_id, "max_times": max_samples})

        local_groups = _local_drop_groups(local)
        output_stages[stage_id] = {
            "stage_id": stage_id,
            "code": local.get("code"),
            "zone_id": local.get("zoneId"),
            "stage_type": stage_type,
            "coverage_category": category,
            "local": {
                "ap_cost": local.get("apCost"),
                "ap_fail_return": local.get("apFailReturn"),
                "exp_gain": local.get("expGain"),
                "gold_gain": local.get("goldGain"),
                "diamond_once_drop": local.get("diamondOnceDrop"),
                "display_drop_groups": local_groups,
                "first_clear": {
                    "diamond_once_drop": local.get("diamondOnceDrop"),
                    "complete_display_entries": local_groups.get("COMPLETE", []),
                    "once_display_entries": local_groups.get("ONCE", []),
                    "warning": "display entries usually omit authoritative counts",
                },
            },
            "penguin": {
                "stage_metadata_matched_by_exact_id": penguin is not None,
                "metadata": penguin,
                "matrix_observations": observations,
                "max_sample_times": max_samples,
            },
            "validation": {"resource_version_mismatches": mismatches},
        }

    local_ids = set(local_stages)
    penguin_ids = set(penguin_by_id)
    matrix_ids = set(matrix_by_stage)
    fetched_ms = int(datetime.fromisoformat(fetched_at).timestamp() * 1000)
    closed_activity_ids = {
        str(stage["stageId"])
        for stage in penguin_stages
        if stage.get("stageType") == "ACTIVITY"
        and (((stage.get("existence") or {}).get("CN") or {}).get("closeTime") or fetched_ms + 1)
        < fetched_ms
    }
    report = {
        "local_stage_count": len(local_ids),
        "penguin_stage_count": len(penguin_ids),
        "matrix_row_count": len(matrix),
        "exact_id_metadata_matches": len(local_ids & penguin_ids),
        "exact_id_matrix_matches": len(local_ids & matrix_ids),
        "metadata_match_rate": len(local_ids & penguin_ids) / len(local_ids),
        "matrix_match_rate": len(local_ids & matrix_ids) / len(local_ids),
        "coverage": dict(sorted(coverage.items())),
        "stage_type_coverage": dict(sorted(stage_type_coverage.items())),
        "missing_penguin_metadata": missing_metadata,
        "missing_matrix_statistics": missing_matrix,
        "penguin_only_stage_ids": sorted(penguin_ids - local_ids),
        "matrix_only_stage_ids": sorted(matrix_ids - local_ids),
        "low_sample_threshold": low_sample,
        "low_sample_stages": low_sample_stages,
        "resource_version_mismatches": version_mismatches,
        "stages_with_multi_quantity_evidence": multiple_quantity,
        "matched_stages_without_penguin_drop_rules": matched_without_drop_rules,
        "penguin_drop_type_entry_counts": dict(sorted(drop_type_counts.items())),
        "expired_activity_history": {
            "penguin_closed_activity_stages": len(closed_activity_ids),
            "exact_id_local_matches": len(closed_activity_ids & local_ids),
            "exact_id_matrix_matches": len(closed_activity_ids & local_ids & matrix_ids),
        },
        "matrix_time": {
            "earliest_start": min((row.get("start") for row in matrix if row.get("start") is not None), default=None),
            "latest_closed_end": max((row.get("end") for row in matrix if row.get("end") is not None), default=None),
            "open_interval_rows": sum(row.get("end") is None for row in matrix),
        },
    }
    dataset = {
        "schema_version": 1,
        "generated_at": fetched_at,
        "runtime_status": "research_only_not_connected_to_battle_settlement",
        "region": "CN",
        "license": {
            "penguin_statistics": "CC BY-NC 4.0",
            "attribution": ATTRIBUTION,
            "redistribution_review_required": True,
        },
        "sources": source_metadata,
        "semantics": {
            "quantity": "total number of items observed in the interval",
            "times": "stage play samples used for that matrix element",
            "quantity_divided_by_times": "mean item quantity per run, not a drop probability",
            "mutual_exclusion": "cannot be inferred from marginal matrix rows",
            "first_clear": "not supplied by Penguin matrix; retained from local stage_table only",
        },
        "items": item_by_id,
        "stages": output_stages,
        "report": report,
    }
    return dataset, report


def main():
    parser = argparse.ArgumentParser(description="Build a research-only CN stage drop dataset")
    parser.add_argument("--output-dir", type=Path, default=Path("drop_data"))
    parser.add_argument("--source-dir", type=Path, help="reuse a previous output's raw directory")
    parser.add_argument("--stage-table", type=Path, default=Path(STAGE_TABLE))
    parser.add_argument("--low-sample", type=int, default=LOW_SAMPLE_DEFAULT)
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()

    fetched_at = datetime.now(timezone.utc).isoformat()
    acquired, errors = acquire_sources(args.output_dir, args.source_dir, args.timeout)
    source_metadata = {name: value["metadata"] for name, value in acquired.items()}
    source_metadata["dataset_built_at"] = fetched_at
    source_metadata["errors"] = errors
    if errors:
        _write_json(args.output_dir / "fetch_errors.json", source_metadata, pretty=True)
    required = {"stages", "matrix", "items"}
    if not required.issubset(acquired):
        raise SystemExit("required source download failed; see fetch_errors.json")

    local_raw = args.stage_table.read_bytes()
    local_table = json.loads(local_raw)
    source_metadata["local_stage_table"] = {
        "path": args.stage_table.as_posix(),
        "sha256": _sha256_bytes(local_raw),
        "bytes": len(local_raw),
    }
    stages = acquired["stages"]["value"]
    matrix_value = acquired["matrix"]["value"]
    matrix = matrix_value["matrix"] if isinstance(matrix_value, dict) else matrix_value
    items = acquired["items"]["value"]
    dataset, report = build_dataset(
        local_table, stages, matrix, items, source_metadata, fetched_at, args.low_sample
    )
    _write_json(args.output_dir / "stage_drop_dataset_cn.json", dataset)
    _write_json(args.output_dir / "coverage_report_cn.json", report, pretty=True)
    print(json.dumps({
        "dataset": str(args.output_dir / "stage_drop_dataset_cn.json"),
        "report": str(args.output_dir / "coverage_report_cn.json"),
        "local_stage_count": report["local_stage_count"],
        "metadata_match_rate": report["metadata_match_rate"],
        "matrix_match_rate": report["matrix_match_rate"],
        "source_errors": errors,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
