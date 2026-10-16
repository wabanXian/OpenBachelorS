import argparse
import json
from pathlib import Path

from ..util.player_preset import (
    POST_TUTORIAL_COMPLETED_STAGES,
    POST_TUTORIAL_GUIDE_GROUPS,
    POST_TUTORIAL_GUIDE_MISSIONS,
    build_post_tutorial_reward_ledger,
)


DEFAULT_OUTPUT = "doc/post_tutorial_reward_audit.json"


def build_audit():
    rewards, unresolved = build_post_tutorial_reward_ledger()
    return {
        "preset": "post_tutorial",
        "initializationMode": "fresh_account_before_tutorial",
        "completedStages": POST_TUTORIAL_COMPLETED_STAGES,
        "completedGuideMissionGroups": POST_TUTORIAL_GUIDE_GROUPS,
        "completedGuideMissions": POST_TUTORIAL_GUIDE_MISSIONS,
        "rewards": rewards,
        "unresolvedDeterministicRewards": unresolved,
        "excludedRewards": [],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate the auditable fresh-account post_tutorial ledger"
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(build_audit(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(output)


if __name__ == "__main__":
    main()
