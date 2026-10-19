"""Generate ordinary battle settlement rules and a compact coverage report."""

import argparse
import json
from pathlib import Path

from ..const.filepath import (
    GENERATED_STAGE_SETTLEMENT_RULES,
    GENERATED_TRAINING_SETTLEMENT_RULES,
    MISSION_TABLE,
    STAGE_DROP_DATASET,
    STAGE_SETTLEMENT_COVERAGE_REPORT,
    STAGE_SETTLEMENT_RULES,
    STAGE_TABLE,
    TRAINING_SETTLEMENT_COVERAGE_REPORT,
)
from ..util.settlement_rule_builder import (
    build_settlement_rules,
    build_training_settlement_rules,
)


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(target)


def generate():
    stage_table = _load(STAGE_TABLE)
    mission_table = _load(MISSION_TABLE)
    rules = build_settlement_rules(
        stage_table,
        mission_table,
        _load(STAGE_DROP_DATASET),
        _load(STAGE_SETTLEMENT_RULES),
    )
    _write(GENERATED_STAGE_SETTLEMENT_RULES, rules)
    _write(
        STAGE_SETTLEMENT_COVERAGE_REPORT,
        {
            "schemaVersion": rules["schemaVersion"],
            "selection": rules["selection"],
            "license": rules["license"],
            **rules["report"],
        },
    )
    training = build_training_settlement_rules(
        stage_table, mission_table, _load(STAGE_SETTLEMENT_RULES)
    )
    _write(GENERATED_TRAINING_SETTLEMENT_RULES, training)
    _write(
        TRAINING_SETTLEMENT_COVERAGE_REPORT,
        {
            "schemaVersion": training["schemaVersion"],
            "selection": training["selection"],
            **training["report"],
        },
    )
    return rules, training


def main():
    parser = argparse.ArgumentParser()
    parser.parse_args()
    rules, training = generate()
    print(
        json.dumps(
            {"ordinary": rules["report"], "training": training["report"]},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
