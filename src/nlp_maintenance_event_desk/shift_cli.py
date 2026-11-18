import argparse
import json
from pathlib import Path

from .handover import IncidentHistory, RecoveryReview, ShiftPlanner


def generate(history, roster):
    incidents = IncidentHistory(roster['machines'], history['events'])
    return ShiftPlanner(incidents, RecoveryReview(roster['roster']), history['released']).current_plan()


def main():
    parser = argparse.ArgumentParser(description='查询设备交班与排产资格')
    parser.add_argument('--history', type=Path, default=Path('fixtures/history.json'))
    parser.add_argument('--registry', type=Path, default=Path('fixtures/business.json'))
    args = parser.parse_args()
    plan = generate(json.loads(args.history.read_text()), json.loads(args.registry.read_text()))
    print(json.dumps(plan, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
