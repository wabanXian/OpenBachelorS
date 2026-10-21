from copy import deepcopy

import pytest

from openbachelors.tool.activity_gacha_audit import build_audit


def _tables():
    activity = {
        "basicInfo": {
            "act_alpha": {
                "type": "SIDE_STORY", "startTime": 100, "endTime": 300
            },
            "act_beta": {
                "type": "SIDE_STORY", "startTime": 600, "endTime": 800,
                "gachaPoolIds": ["BETA"]
            },
            "act_missing": {
                "type": "MINI", "startTime": None, "endTime": None
            },
        },
        "activity": {
            "SIDE_STORY": {
                "act_alpha": {"detail": "activity state"},
                "act_beta": {},
            }
        },
    }
    gacha = {
        "gachaPoolClient": [
            {
                "gachaPoolId": "STANDARD", "gachaPoolName": "standard",
                "gachaRuleType": "NORMAL", "openTime": 200, "endTime": 900
            },
            {
                "gachaPoolId": "BETA", "gachaPoolName": "event banner",
                "gachaRuleType": "SINGLE", "openTime": 620, "endTime": 720
            },
        ]
    }
    stages = {
        "stages": {
            "one": {"zoneId": "act_alpha_zone1"},
            "two": {"zoneId": "not_act_alpha_zone1"},
        }
    }
    return activity, gacha, stages


def test_time_overlap_is_not_automatically_a_verified_pair():
    activity, gacha, stages = _tables()
    before = deepcopy((activity, gacha, stages))
    report = build_audit(activity, gacha, stages)
    rows = {row["activityId"]: row for row in report["activities"]}

    assert rows["act_alpha"]["status"] == "candidates_only_not_verified"
    assert rows["act_alpha"]["explicitPoolIds"] == []
    assert rows["act_alpha"]["overlapCandidates"][0]["poolId"] == "STANDARD"
    assert rows["act_alpha"]["overlapCandidates"][0]["evidence"] == "time_overlap_only"
    assert rows["act_alpha"]["zonePrefixCandidates"] == ["act_alpha_zone1"]
    assert rows["act_beta"]["status"] == "explicit_pool_id_in_activity_data"
    assert rows["act_beta"]["explicitPoolIds"] == ["BETA"]
    assert rows["act_missing"]["status"] == "no_pool_evidence"
    assert report["accountChanges"] is False
    assert (activity, gacha, stages) == before


def test_named_pool_id_nested_in_activity_runtime_is_recognized():
    activity, gacha, _ = _tables()
    activity["activity"]["SIDE_STORY"]["act_alpha"] = {
        "some": {"gacha_pool_id": "STANDARD"}
    }
    report = build_audit(activity, gacha)
    alpha = next(row for row in report["activities"] if row["activityId"] == "act_alpha")
    assert alpha["explicitPoolIds"] == ["STANDARD"]


def test_invalid_windows_do_not_produce_false_pairing():
    activity, gacha, _ = _tables()
    activity["basicInfo"]["act_alpha"]["endTime"] = 90
    report = build_audit(activity, gacha, candidate_limit=0)
    alpha = next(row for row in report["activities"] if row["activityId"] == "act_alpha")
    assert alpha["poolOverlapCount"] == 0
    assert alpha["overlapCandidates"] == []


def test_duplicate_pool_ids_and_invalid_limits_are_rejected():
    activity, gacha, _ = _tables()
    with pytest.raises(ValueError, match="candidate_limit"):
        build_audit(activity, gacha, candidate_limit=-1)
    gacha["gachaPoolClient"].append(deepcopy(gacha["gachaPoolClient"][0]))
    with pytest.raises(ValueError, match="duplicate gacha pool ID"):
        build_audit(activity, gacha)
