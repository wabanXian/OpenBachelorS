"""Conservative, persistent settlement for ordinary quest battles.

The client battle log is evidence supplied by the client, not an independently
simulated proof.  Only result encodings observed in real protocol captures are
accepted here.
"""

from copy import deepcopy
from hashlib import sha256
import json
from uuid import uuid4

from ..const.filepath import STAGE_TABLE
from .const_json_loader import const_json_loader
from .faketime import faketime
from .favor import build_squad_favor_snapshot, plan_battle_favor
from .mainline_unlock import unlock_conditions_for
from .mission_lifecycle import plan_mission_events
from .campaign_lifecycle import plan_campaign_unlocks
from .player_progression import plan_player_exp_gain
from .reward_grant import plan_reward
from .settlement_rules import get_stage_rule


CLIENT_BATTLE_ID = "00000000-0000-0000-0000-000000000000"
PASS_STATE = 2
COMPLETE_STATE = 3
SESSION_KEY = "quest_battle_sessions_v1"
MAX_SETTLED_SESSIONS = 100


def main_one_mission_record():
    return {"progress": [{"target": 1, "value": 1}], "state": 2}


def completed_unclaimed_mission_record():
    return {"progress": [{"target": 1, "value": 1}], "state": 2}


def repair_missing_main_one_mission(player_data):
    """Expose the unclaimed 0-1 mission for saves settled before support."""
    stages = player_data["dungeon"]["stages"]
    if "main_00-01" not in stages or stages["main_00-01"]["state"] < PASS_STATE:
        return False
    missions = player_data["mission"]["missions"]["MAIN"]
    if "main_1" in missions:
        if missions["main_1"]["state"] != 1:
            return False
        missions["main_1"] = main_one_mission_record()
        player_data["mission"]["missionGroups"]["main_1"] = 0
        return True
    missions["main_1"] = main_one_mission_record()
    player_data["mission"]["missionGroups"]["main_1"] = 0
    return True


def _stable_fraction(seed):
    return int(sha256(seed.encode()).hexdigest()[:16], 16) / 2**64


def _approximate_drops(rule, session):
    response_fields = {"rewards": [], "additionalRewards": []}
    audit = []
    for drop in rule["dropRules"]:
        mean = drop["quantity"] / drop["times"]
        floor = int(mean)
        total = floor + int(
            _stable_fraction(
                f'{session["sessionId"]}:{session["finishFingerprint"]}:{drop["id"]}'
            )
            < mean - floor
        )
        lower_sum = sum(
            value for value in (entry.get("lower") for entry in drop["classes"])
            if isinstance(value, int)
        )
        upper_values = [entry.get("upper") for entry in drop["classes"]]
        if upper_values and all(isinstance(value, int) for value in upper_values):
            total = min(total, sum(upper_values))
        total = max(total, lower_sum)
        allocation = []
        remaining = total
        for entry in drop["classes"]:
            count = min(remaining, entry["lower"] or 0)
            allocation.append(count)
            remaining -= count
        for index, entry in enumerate(drop["classes"]):
            if remaining <= 0:
                break
            capacity = (
                remaining
                if entry.get("upper") is None
                else max(0, entry["upper"] - allocation[index])
            )
            added = min(remaining, capacity)
            allocation[index] += added
            remaining -= added
        if remaining and allocation:
            allocation[0] += remaining
        for entry, count in zip(drop["classes"], allocation):
            if count:
                response_fields[entry["responseField"]].append(
                    {"type": drop["type"], "id": drop["id"], "count": count}
                )
        audit.append(
            {
                "itemId": drop["id"],
                "algorithm": drop["algorithm"],
                "quantity": drop["quantity"],
                "times": drop["times"],
                "mean": mean,
                "selectedTotal": total,
                "allocation": allocation,
                "source": drop["source"],
                "warning": drop["warning"],
            }
        )
    return response_fields["rewards"], response_fields["additionalRewards"], audit


def _stage_record(stage_id, state=0):
    return {
        "stageId": stage_id,
        "completeTimes": 0,
        "startTimes": 0,
        "practiceTimes": 0,
        "state": state,
        "hasBattleReplay": 0,
        "noCostCnt": 0,
    }


def _store(extra_save):
    return extra_save.save_obj.setdefault(
        SESSION_KEY, {"active": None, "settled": {}}
    )


def _fingerprint(value):
    if isinstance(value, str):
        raw = value.encode()
    else:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return sha256(raw).hexdigest()


def _set_path(player_data, path, value):
    target = player_data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = deepcopy(value)


def _apply_mutations(player_data, mutations):
    for mutation in mutations:
        _set_path(player_data, mutation["path"], mutation["value"])


def reconcile_pending_session(player_data):
    """Finish an interrupted write using idempotent absolute assignments."""
    store = player_data.extra_save.save_obj.get(SESSION_KEY)
    if not store:
        return None
    session = store.get("active")
    if not session or session.get("phase") not in {"starting", "settling"}:
        return None
    _apply_mutations(player_data, session["mutations"])
    if session["phase"] == "starting":
        session["phase"] = "active"
        return "start_recovered"
    session["phase"] = "settled"
    _archive_settled(player_data.extra_save, session)
    return "settlement_recovered"


def _archive_settled(extra_save, session):
    store = _store(extra_save)
    settled = store["settled"]
    settled[session["finishFingerprint"]] = {
        "sessionId": session["sessionId"],
        "stageId": session["stageId"],
        "response": deepcopy(session["response"]),
    }
    while len(settled) > MAX_SETTLED_SESSIONS:
        del settled[next(iter(settled))]
    store["active"] = None


def _start_key(request_json):
    return _fingerprint(
        {
            "stageId": request_json.get("stageId"),
            "startTs": request_json.get("startTs"),
            "battleType": request_json.get("battleType"),
            "usePracticeTicket": request_json.get("usePracticeTicket", 0),
        }
    )


def prepare_battle_start(player_data, request_json):
    stage_id = request_json.get("stageId")
    stage_table = const_json_loader[STAGE_TABLE]["stages"]
    if stage_id not in stage_table:
        return None, "unknown_stage"
    rule, rule_source = get_stage_rule(stage_id)
    if rule is None:
        return None, rule_source
    if request_json.get("usePracticeTicket", 0):
        return None, "practice_settlement_not_implemented"
    if stage_id not in player_data["dungeon"]["stages"]:
        return None, "stage_not_unlocked"
    if not all(_condition_met(player_data, item) for item in rule["unlockCondition"]):
        return None, "stage_unlock_condition_not_met"
    squad_snapshot, snapshot_error = build_squad_favor_snapshot(
        player_data, request_json
    )
    if snapshot_error:
        return None, snapshot_error

    store = _store(player_data.extra_save)
    key = _start_key(request_json)
    active = store.get("active")
    if active and active.get("startKey") == key and active.get("phase") == "active":
        return active, "duplicate_start"
    if active and active.get("phase") in {"starting", "active", "settling"}:
        return None, "active_session_exists"

    ap_cost = max(0, rule["apCost"])
    ap_before = player_data["status"]["ap"]
    if ap_before < ap_cost:
        return None, "insufficient_ap"
    record = player_data["dungeon"]["stages"][stage_id]
    mutations = [
        {"path": ["status", "ap"], "value": ap_before - ap_cost},
        {
            "path": ["dungeon", "stages", stage_id, "startTimes"],
            "value": record["startTimes"] + 1,
        },
    ]
    # Spending from a full bar starts the regeneration clock. When the bar was
    # already below max, preserve its partial interval instead of postponing
    # the next point on every battle start.
    if ap_cost and ap_before >= player_data["status"]["maxAp"]:
        mutations.insert(
            1,
            {"path": ["status", "lastApAddTime"], "value": int(faketime())},
        )
    session = {
        "sessionId": str(uuid4()),
        "battleId": CLIENT_BATTLE_ID,
        "stageId": stage_id,
        "startKey": key,
        "phase": "starting",
        "apBefore": ap_before,
        "apCost": ap_cost,
        "apFailReturn": max(0, rule["apFailReturn"]),
        "ruleSource": rule_source,
        "ruleCompleteness": rule["ruleCompleteness"],
        "squadSnapshot": squad_snapshot,
        "assistFriend": request_json.get("assistFriend"),
        "isReplay": int(request_json.get("isReplay", 0) or 0),
        "mutations": mutations,
    }
    store["active"] = session
    return session, "new_session"


def activate_battle_start(player_data, session):
    _apply_mutations(player_data, session["mutations"])
    session["phase"] = "active"


def classify_result(decoded):
    evidence = {
        key: decoded.get(key)
        for key in ("stageId", "battleId", "interrupt", "giveUp", "completeState",
                    "percent", "killCnt")
    }
    if decoded.get("interrupt") or decoded.get("giveUp"):
        return "interrupted", None, evidence
    state = decoded.get("completeState")
    if state in (PASS_STATE, COMPLETE_STATE):
        return "success", state, evidence
    if state == 1:
        return "failed", None, evidence
    return "unknown", None, evidence


def _condition_met(player_data, condition):
    stage_id = condition["stageId"]
    if stage_id not in player_data["dungeon"]["stages"]:
        return False
    state = player_data["dungeon"]["stages"][stage_id]["state"]
    required = condition["completeState"]
    return state >= (COMPLETE_STATE if required == "COMPLETE" else PASS_STATE)


def _newly_unlocked(player_data, future_stage_states):
    stage_table = const_json_loader[STAGE_TABLE]["stages"]
    unlocked = []
    # Evaluate against a lightweight state view that includes this settlement.
    def state_for(stage_id):
        if stage_id in future_stage_states:
            return future_stage_states[stage_id]
        if stage_id in player_data["dungeon"]["stages"]:
            return player_data["dungeon"]["stages"][stage_id]["state"]
        return None

    for candidate_id, candidate in stage_table:
        if candidate_id in player_data["dungeon"]["stages"]:
            continue
        conditions = unlock_conditions_for(candidate_id, candidate)
        if not conditions:
            continue
        ok = True
        for condition in conditions:
            state = state_for(condition["stageId"])
            threshold = COMPLETE_STATE if condition["completeState"] == "COMPLETE" else PASS_STATE
            if state is None or state < threshold:
                ok = False
                break
        if ok:
            unlocked.append(candidate_id)
    return unlocked


def _base_response(result=0):
    return {
        "result": result,
        "apFailReturn": 0,
        "expScale": 1.0,
        "goldScale": 1.0,
        "rewards": [],
        "firstRewards": [],
        "unlockStages": [],
        "unusualRewards": [],
        "additionalRewards": [],
        "furnitureRewards": [],
        "overrideRewards": [],
        "alert": [],
        "suggestFriend": False,
        "pryResult": [],
        "itemReturn": [],
        "extra": {"sixStar": {"groupId": "main_15", "before": 32, "after": 32, "stageBefore": 2}},
    }


def reject_response(reason):
    response = _base_response(result=1)
    response["settlementError"] = reason
    return response


def _normalise_character_reward_payloads(response, player_data):
    """Add the acquisition payload required by the client for CHAR rewards.

    A CHAR is not a batchable inventory item. The client dereferences
    ``charGet`` while building the battle-finish drop view, so the generic
    ``{type, id, count}`` shape used for materials is invalid here. Keep this
    repair on duplicate responses as well: settlements written by older builds
    may already be safely committed but still have the incompatible cached
    response in the write-ahead journal.
    """
    char_inst_ids = {
        char["charId"]: int(char["instId"])
        for _, char in player_data["troop"]["chars"]
    }
    for field in ("rewards", "firstRewards", "additionalRewards", "unusualRewards"):
        for item in response.get(field, []):
            if item.get("type") != "CHAR" or item.get("charGet") is not None:
                continue
            char_id = item.get("id")
            char_inst_id = char_inst_ids.get(char_id)
            if char_inst_id is None:
                continue
            item.pop("count", None)
            item["charGet"] = {
                "charInstId": char_inst_id,
                "charId": char_id,
                "isNew": 1,
            }
    return response


def prepare_battle_finish(player_data, decoded, raw_data):
    store = _store(player_data.extra_save)
    # Canonical decoded content survives a transport retry that re-encrypts
    # the same result with a different IV, while begin/end timestamps still
    # distinguish separate battles.
    fingerprint = _fingerprint(decoded)
    session = store.get("active")
    if fingerprint in store["settled"] and session and session.get("phase") == "active":
        return {
            "kind": "rejected",
            "reason": "replayed_settled_result_for_active_session",
        }
    if fingerprint in store["settled"]:
        cached = store["settled"][fingerprint]
        response = _normalise_character_reward_payloads(
            deepcopy(cached["response"]), player_data
        )
        return {"kind": "duplicate", "response": response}

    if not session or session.get("phase") != "active":
        return {"kind": "rejected", "reason": "missing_active_session"}
    if decoded.get("stageId") != session["stageId"]:
        return {"kind": "rejected", "reason": "stage_mismatch"}
    if decoded.get("battleId") != session["battleId"]:
        return {"kind": "rejected", "reason": "battle_id_mismatch"}
    session["finishFingerprint"] = fingerprint

    outcome, stars, evidence = classify_result(decoded)
    if outcome == "unknown":
        return {"kind": "rejected", "reason": "unknown_result", "evidence": evidence}

    stage_id = session["stageId"]
    stage = const_json_loader[STAGE_TABLE]["stages"][stage_id]
    rule, rule_source = get_stage_rule(stage_id)
    if rule is None:
        return {"kind": "rejected", "reason": rule_source}
    record = player_data["dungeon"]["stages"][stage_id]
    response = _base_response()
    mutations = []
    rewards = {"status": [], "items": []}
    before = record.copy()

    if outcome != "success":
        refund = min(session["apCost"], session["apFailReturn"])
        response["apFailReturn"] = refund
        mutations.append({"path": ["status", "ap"], "value": player_data["status"]["ap"] + refund})
    else:
        old_state = record["state"]
        new_state = max(old_state, stars)
        mutations.extend(
            [
                {"path": ["dungeon", "stages", stage_id, "state"], "value": new_state},
                {"path": ["dungeon", "stages", stage_id, "completeTimes"], "value": record["completeTimes"] + 1},
                {"path": ["status", "gold"], "value": player_data["status"]["gold"] + rule["goldGain"]},
            ]
        )
        progression_mutations, player_progression = plan_player_exp_gain(
            player_data, rule["expGain"]
        )
        mutations.extend(progression_mutations)
        rewards["status"].extend(
            [
                {"field": "exp", "count": rule["expGain"]},
                {"field": "gold", "count": rule["goldGain"]},
            ]
        )
        rewards["playerProgression"] = player_progression

        # Since the 2025-12 CN rules change, ordinary successful operations
        # grant every account-owned squad member trust equal to actual sanity
        # consumed. This engine does not handle annihilation or multi-run
        # protocols, so the persisted session's charged AP is the actual cost.
        favor_mutations, favor_audit = plan_battle_favor(
            player_data, session.get("squadSnapshot", []), session["apCost"]
        )
        mutations.extend(favor_mutations)
        rewards["favor"] = favor_audit

        if old_state < PASS_STATE:
            for reward_rule in rule["firstPassRewards"]:
                audit_item = {
                    "type": reward_rule["type"],
                    "id": reward_rule["id"],
                    "count": reward_rule["count"],
                }
                try:
                    response["firstRewards"].append(
                        plan_reward(
                            player_data,
                            mutations,
                            reward_rule,
                            decoded.get("platform", 2),
                        )
                    )
                except ValueError as exc:
                    return {"kind": "rejected", "reason": str(exc)}
                rewards["items"].append(audit_item)
        if old_state < COMPLETE_STATE and new_state >= COMPLETE_STATE and rule["diamondOnceDrop"]:
            diamond = {"type": "DIAMOND", "id": "4002", "count": rule["diamondOnceDrop"]}
            response["firstRewards"].append(
                plan_reward(player_data, mutations, diamond, decoded.get("platform", 2))
            )
            rewards["items"].append(diamond)

        normal_drops, additional_drops, approximations = _approximate_drops(
            rule, session
        )
        response["rewards"] = normal_drops
        response["additionalRewards"] = additional_drops
        for item in normal_drops + additional_drops:
            plan_reward(player_data, mutations, item, decoded.get("platform", 2))
            rewards["items"].append(item)
        rewards["approximations"] = approximations

        future = {stage_id: new_state}
        unlocked = _newly_unlocked(player_data, future)
        for unlocked_id in unlocked:
            mutations.append({"path": ["dungeon", "stages", unlocked_id], "value": _stage_record(unlocked_id)})
        response["unlockStages"] = unlocked
        campaign_mutations, campaign_changes = plan_campaign_unlocks(
            player_data, future
        )
        mutations.extend(campaign_mutations)
        rewards["campaignChanges"] = campaign_changes
        if stage["stageType"] == "MAIN":
            mutations.append({"path": ["status", "mainStageProgress"], "value": stage_id})

    if outcome == "success":
        mission_mutations, mission_changes = plan_mission_events(
            player_data,
            [
                (
                    "battle_success",
                    {
                        "stageId": session["stageId"],
                        "stars": stars,
                        "killCnt": evidence.get("killCnt", 0),
                        "usedAssist": bool(session.get("assistFriend")),
                        "isReplay": bool(session.get("isReplay")),
                    },
                ),
                ("battle_result", {"killCnt": evidence.get("killCnt", 0)}),
            ],
            event_id=session["sessionId"],
        )
        mutations.extend(mission_mutations)
        rewards["missionChanges"] = mission_changes

    session.update(
        {
            "phase": "settling",
            "finishFingerprint": fingerprint,
            "outcome": outcome,
            "stars": stars,
            "evidence": evidence,
            "beforeStage": before,
            "mutations": mutations,
            "response": response,
            "rewards": rewards,
            "ruleSource": rule_source,
            "ruleCompleteness": rule["ruleCompleteness"],
        }
    )
    return {"kind": "prepared", "session": session, "response": response}


def commit_battle_finish(player_data, session):
    _apply_mutations(player_data, session["mutations"])
    session["phase"] = "settled"
    _archive_settled(player_data.extra_save, session)

