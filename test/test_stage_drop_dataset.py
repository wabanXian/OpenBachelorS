from pathlib import Path

from openbachelors.tool.stage_drop_dataset import acquire_sources, build_dataset


def test_source_failures_are_returned_without_fabricated_data(monkeypatch):
    def fail_download(url, timeout):
        raise ConnectionError("offline")

    monkeypatch.setattr(
        "openbachelors.tool.stage_drop_dataset._download_json", fail_download
    )
    acquired, errors = acquire_sources(Path("test/.unused_drop_output"))
    assert acquired == {}
    assert len(errors) == 3
    assert all(error["error_type"] == "ConnectionError" for error in errors)


def test_exact_id_matching_and_statistical_semantics():
    local = {
        "stages": {
            "main_00-01": {
                "stageId": "main_00-01",
                "code": "0-1",
                "zoneId": "main_0",
                "stageType": "MAIN",
                "apCost": 6,
                "apFailReturn": 6,
                "expGain": 60,
                "goldGain": 60,
                "diamondOnceDrop": 1,
                "stageDropInfo": {
                    "displayDetailRewards": [
                        {"type": "MATERIAL", "id": "2001", "dropType": "NORMAL", "occPercent": "ALWAYS"},
                        {"type": "DIAMOND", "id": "4002", "dropType": "COMPLETE", "occPercent": "ALWAYS"},
                    ]
                },
            },
            "same-name-wrong-id": {
                "stageId": "same-name-wrong-id",
                "code": "0-1",
                "zoneId": "other",
                "stageType": "ACTIVITY",
                "apCost": 6,
                "stageDropInfo": None,
            },
        }
    }
    penguin_stages = [{
        "stageId": "main_00-01",
        "code": "0-1",
        "zoneId": "main_0",
        "stageType": "MAIN",
        "apCost": 6,
        "dropInfos": [{
            "itemId": "2001",
            "dropType": "NORMAL_DROP",
            "bounds": {"lower": 1, "upper": 2},
        }],
    }]
    matrix = [{
        "stageId": "main_00-01",
        "itemId": "2001",
        "quantity": 150,
        "times": 100,
        "start": 1,
        "end": None,
    }]
    dataset, report = build_dataset(
        local, penguin_stages, matrix, [{"itemId": "2001", "name": "record"}],
        {}, "2026-01-01T00:00:00+00:00", 101
    )

    assert report["local_stage_count"] == 2
    assert report["exact_id_metadata_matches"] == 1
    assert "same-name-wrong-id" in report["missing_penguin_metadata"]
    observation = dataset["stages"]["main_00-01"]["penguin"]["matrix_observations"][0]
    assert observation["quantity"] == 150
    assert observation["times"] == 100
    assert observation["multi_quantity_possible"] is True
    assert observation["guarantee_from_bounds"] == "guaranteed"
    assert observation["quantity_rule_from_bounds"] == "guaranteed_quantity_range"
    assert observation["ratio_semantics"] == "mean_item_quantity_per_run_not_probability"
    assert observation["mutual_exclusion"] == "not_determinable_from_marginal_statistics"
    assert dataset["stages"]["main_00-01"]["local"]["first_clear"]["diamond_once_drop"] == 1
    assert report["low_sample_stages"] == [{"stage_id": "main_00-01", "max_times": 100}]


def test_version_mismatch_and_drop_classification_are_reported():
    local = {"stages": {"weekly_1": {
        "stageId": "weekly_1", "code": "LS-1", "zoneId": "weekly_1",
        "stageType": "DAILY", "apCost": 10, "stageDropInfo": None,
    }}}
    penguin = [{
        "stageId": "weekly_1", "code": "LS-1", "zoneId": "weekly_1",
        "stageType": "DAILY", "apCost": 20,
        "dropInfos": [{"itemId": "4001", "dropType": "NORMAL_DROP", "bounds": {"lower": 0, "upper": 1}}],
    }]
    matrix = [{"stageId": "weekly_1", "itemId": "4001", "quantity": 2, "times": 10, "start": 1, "end": 2}]
    dataset, report = build_dataset(
        local, penguin, matrix, [], {}, "2026-01-01T00:00:00+00:00", 1
    )

    assert report["coverage"]["resource"] == {"local": 1, "metadata": 1, "matrix": 1}
    assert report["resource_version_mismatches"][0]["fields"][0]["field"] == "apCost"
    row = dataset["stages"]["weekly_1"]["penguin"]["matrix_observations"][0]
    assert row["drop_infos"][0]["drop_type"] == "NORMAL_DROP"
    assert row["guarantee_from_bounds"] == "not_guaranteed"
    assert row["quantity_rule_from_bounds"] == "probabilistic_or_zero_quantity"
