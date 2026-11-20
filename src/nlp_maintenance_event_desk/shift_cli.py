import argparse
import json
from pathlib import Path

from .parsing import (
    apply_corrections,
    merge_observations,
    parse_records,
    resolve_trial_anchors,
    structured_observations,
)
from .plan import build_plan, issue_sheets, load_sheets


def load_observations(history_path, record_paths, registry):
    """合并已核对的技术记录与中文交接记录，得到统一的观察集合。"""
    observations = []
    warnings = []
    if history_path:
        history = json.loads(Path(history_path).read_text(encoding='utf-8'))
        observations.extend(structured_observations(history.get('events', [])))
    else:
        history = {}
    records = []
    for path in record_paths or []:
        records.extend(json.loads(Path(path).read_text(encoding='utf-8')))
    if records:
        parsed, corrections, found = parse_records(records, registry)
        warnings.extend(found)
        observations.extend(parsed)
        warnings.extend(apply_corrections(observations, corrections))
    observations = merge_observations(observations)
    warnings.extend(resolve_trial_anchors(observations))
    return observations, history, records, warnings


def current_plan(history_path, record_paths, registry, store_path):
    observations, history, _, warnings = load_observations(history_path, record_paths, registry)
    released = list(history.get('released', [])) + load_sheets(store_path)
    plan = build_plan(observations, registry, released, warnings)
    return plan, observations, history


def generate(history, registry):
    """仅按已核对的技术记录给出各设备当前排产资格。"""
    observations = structured_observations(history.get('events', []))
    return build_plan(observations, registry, history.get('released', []))['machines']


def main(argv=None):
    parser = argparse.ArgumentParser(description='查询设备交班与排产资格')
    parser.add_argument('action', nargs='?', default='plan', choices=['plan', 'issue', 'sheets'])
    parser.add_argument('--history', type=Path, default=Path('fixtures/history.json'))
    parser.add_argument('--registry', type=Path, default=Path('fixtures/business.json'))
    parser.add_argument('--records', type=Path, nargs='*', default=[Path('fixtures/records.json')])
    parser.add_argument('--store', type=Path, default=Path('var/handovers.json'))
    args = parser.parse_args(argv)
    registry = json.loads(args.registry.read_text(encoding='utf-8'))
    if args.action == 'sheets':
        history = json.loads(args.history.read_text(encoding='utf-8'))
        result = {'seeded': history.get('released', []), 'issued': load_sheets(args.store)}
    elif args.action == 'issue':
        plan, observations, history = current_plan(args.history, args.records, registry, args.store)
        released = list(history.get('released', [])) + load_sheets(args.store)
        result = issue_sheets(plan, observations, released, args.store)
    else:
        plan, _, _ = current_plan(args.history, args.records, registry, args.store)
        result = plan
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
