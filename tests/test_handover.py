import json
from pathlib import Path

from nlp_maintenance_event_desk.shift_cli import generate


def test_recent_episode_changes_current_plan_and_keeps_signed_handover():
    root = Path(__file__).parents[1] / 'fixtures'
    history = json.loads((root / 'history.json').read_text())
    roster = json.loads((root / 'business.json').read_text())
    p4, p5, p6 = generate(history, roster)
    assert p4['clearance'] == 'E-12'
    assert p5['status'] == '待恢复'
    assert p6['status'] == '未停机'
    history['events'].append({'id': 'E-13', 'machine': 'P4', 'kind': '停机', 'occurred': '2026-11-17T10:10:00'})
    p4, _, _ = generate(history, roster)
    assert p4['needs_replan'] and p4['previous']['clearance'] == 'E-12'
