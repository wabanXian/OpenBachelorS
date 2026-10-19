"""Generate the auditable mission-template support report."""

import json
from collections import defaultdict
from pathlib import Path

from ..const.filepath import MISSION_TABLE
from ..util.const_json_loader import const_json_loader
from ..util.mission_lifecycle import mission_spec

HOOKED_EVENTS = {
    "battle_success",
    "battle_result",
    "upgrade_char",
    "squad_formation",
    "delivery_order",
    "normal_gacha",
}


def build_report():
    rows = defaultdict(
        lambda: {
            "count": 0,
            "types": set(),
            "parserSupported": False,
            "operationHooked": False,
        }
    )
    for _, mission in const_json_loader[MISSION_TABLE]["missions"]:
        spec = mission_spec(mission)
        row = rows[mission["template"]]
        row["count"] += 1
        row["types"].add(mission["type"])
        row["parserSupported"] = spec["supported"]
        row["operationHooked"] = (
            spec.get("mode") == "state" or spec.get("event") in HOOKED_EVENTS
        )
    return {
        "source": "res/excel/mission_table.json",
        "templates": {
            name: {**row, "types": sorted(row["types"])}
            for name, row in sorted(rows.items())
        },
        "notes": {
            "eventSemantics": "Only successful server-side operations emit events.",
            "unsupported": "No progress is fabricated; requires an implemented operation/protocol or confirmed parameter semantics.",
        },
    }


def main():
    target = Path("data/mission_template_coverage.json")
    target.write_text(json.dumps(build_report(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
