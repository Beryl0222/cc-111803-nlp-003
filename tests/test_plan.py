import json
from pathlib import Path

from nlp_maintenance_event_desk.interpretation import iter_interpretations
from nlp_maintenance_event_desk.parsing import (
    apply_corrections,
    merge_observations,
    parse_records,
    resolve_trial_anchors,
)
from nlp_maintenance_event_desk.plan import issue_sheets, load_sheets
from nlp_maintenance_event_desk.shift_cli import current_plan

FIXTURES = Path(__file__).parents[1] / 'fixtures'


def registry():
    return json.loads((FIXTURES / 'business.json').read_text(encoding='utf-8'))


def plan_for(record_files, store, registry=None):
    registry = registry or globals()['registry']()
    records = [FIXTURES / name for name in record_files]
    plan, _, _ = current_plan(FIXTURES / 'history.json', records, registry, store)
    return plan


def row(plan, machine):
    return next(item for item in plan['machines'] if item['machine'] == machine)


def parse_text_records(records):
    reg = registry()
    observations, corrections, _ = parse_records(records, reg)
    apply_corrections(observations, corrections)
    observations = merge_observations(observations)
    resolve_trial_anchors(observations)
    return observations, reg


def test_plan_confirms_recovery_and_keeps_alternative_bases(tmp_path):
    plan = plan_for(['records.json'], tmp_path / 'handovers.json')
    p4 = row(plan, 'P4')
    assert p4['status'] == '已签认恢复' and p4['available']
    # 全部解释都支持恢复，但试运行依据不同，差异保留供回查
    assert {(o['trial'], o['signoff']) for o in p4['clearance_options']} == {
        ('E-11', 'E-12'),
        ('log-2#1', 'E-12'),
    }
    assert any('复位后有跑过一段' in excerpt for o in p4['clearance_options'] for excerpt in o['excerpts'])
    p5 = row(plan, 'P5')
    assert p5['status'] == '待恢复' and not p5['available']
    assert [(r['signoff'], r['reason']) for r in p5['rejected_signoffs']] == [
        ('E-33', '检验人不在当班名册')
    ]
    assert row(plan, 'P6')['status'] == '未停机'
    assert plan['executable'] == ['P4', 'P6']


def test_late_records_hold_device_and_keep_others_executable(tmp_path):
    store = tmp_path / 'handovers.json'
    plan_for(['records.json'], store)
    plan = plan_for(['records.json', 'records-late.json'], store)
    p4 = row(plan, 'P4')
    # 晚到的停机记录使 10:40 的签认可能只对应前一次故障：设备保留给班长核对
    assert p4['status'] == '待核' and not p4['available']
    assert p4['causes'] == ['试运行与签认先后']
    assert p4['needs_replan'] and p4['previous']['ref'] == 'handover-8'
    assert {s['record'] for s in p4['pending_confirmation']} == {'REC-81', 'log-7'}
    # P5 的补记签认登记在“上一次复位前”，不能作为恢复依据
    p5 = row(plan, 'P5')
    assert p5['status'] == '待恢复'
    rejected = {r['signoff']: r['reason'] for r in p5['rejected_signoffs']}
    assert rejected['log-8#1'] == '签认与本次停机、试运行的先后关系不成立'
    assert rejected['E-33'] == '检验人不在当班名册'
    # 同班未受影响的设备安排仍可执行
    assert row(plan, 'P6')['available']
    assert plan['executable'] == ['P6']
    assert plan['held'] == ['P4'] and plan['replan'] == ['P4']


def test_one_reset_cannot_clear_two_machines_with_similar_symptoms():
    records = [
        {'record_id': 'r1', 'source': 'shift-log', 'revision': 1, 'text': 'P4 08:00 停机。'},
        {'record_id': 'r2', 'source': 'shift-log', 'revision': 1, 'text': 'P5 08:05 停机。'},
        {'record_id': 'r3', 'source': 'shift-log', 'revision': 1, 'text': '08:30 东侧那台复位后跑过一段。'},
        {'record_id': 'r4', 'source': 'shift-log', 'revision': 1, 'text': '东侧那台 09:00 复检合格已签认，检查员周工。'},
    ]
    observations, reg = parse_text_records(records)
    machines = ['P4', 'P5']
    # 任何解释里一次复位加签认都只能落在一台设备上
    for _, verdicts in iter_interpretations(observations, machines, reg['roster']):
        available = {
            machine: any(v['available'] for _, v in verdicts[machine]) for machine in machines
        }
        assert not all(available.values())
    from nlp_maintenance_event_desk.plan import build_plan

    plan = build_plan(observations, reg, [])
    for machine in machines:
        item = row(plan, machine)
        assert item['status'] == '待核' and not item['available']
        assert item['causes'] == ['设备身份']
        assert {s['record'] for s in item['pending_confirmation']} == {'r3', 'r4'}


def test_correction_pins_device_reference_and_clears_ambiguity():
    records = [
        {'record_id': 'r1', 'source': 'shift-log', 'revision': 1, 'text': 'P4 08:00 停机。'},
        {'record_id': 'r2', 'source': 'shift-log', 'revision': 1, 'text': 'P5 08:05 停机。'},
        {'record_id': 'r3', 'source': 'shift-log', 'revision': 1, 'text': '08:30 东侧那台复位后跑过一段。'},
        {'record_id': 'r4', 'source': 'shift-log', 'revision': 1, 'text': '东侧那台 09:00 复检合格已签认，检查员周工。'},
        {'record_id': 'r5', 'source': 'shift-log', 'revision': 1, 'text': '复核：复位那台是 P4。'},
        {'record_id': 'r6', 'source': 'shift-log', 'revision': 1, 'text': '复核：签认那台是 P4。'},
    ]
    observations, reg = parse_text_records(records)
    from nlp_maintenance_event_desk.plan import build_plan

    plan = build_plan(observations, reg, [])
    assert row(plan, 'P4')['status'] == '已签认恢复'
    assert row(plan, 'P4')['available']
    assert row(plan, 'P5')['status'] == '待恢复'
    assert not row(plan, 'P5')['available']


def test_issued_handover_keeps_events_and_late_records_trigger_replan(tmp_path):
    store = tmp_path / 'handovers.json'
    reg = registry()
    plan, observations, history = current_plan(FIXTURES / 'history.json', [FIXTURES / 'records.json'], reg, store)
    released = list(history['released']) + load_sheets(store)
    result = issue_sheets(plan, observations, released, store)
    refs = {sheet['machine']: sheet for sheet in result['issued']}
    # 交接单看得见当时采用的设备和事件
    assert refs['P4']['status'] == '已签认恢复'
    assert set(refs['P4']['events']) == {'E-10', 'E-11', 'E-12', 'REC-81#1', 'log-2#1'}
    assert set(refs['P4']['records']) == {'log-1', 'log-2', 'REC-81'}
    assert refs['P6']['status'] == '未停机'
    # 结论未变时不重复签发
    plan_again, observations, history = current_plan(
        FIXTURES / 'history.json', [FIXTURES / 'records.json'], reg, store
    )
    again = issue_sheets(plan_again, observations, list(history['released']) + load_sheets(store), store)
    assert again['issued'] == [] and set(again['kept']) == {'handover-9', 'handover-10'}
    # 晚到记录只让 P4 进入待核重排，P6 的交接安排仍可执行
    plan2, _, _ = current_plan(
        FIXTURES / 'history.json',
        [FIXTURES / 'records.json', FIXTURES / 'records-late.json'],
        reg,
        store,
    )
    p4, p6 = row(plan2, 'P4'), row(plan2, 'P6')
    assert p4['needs_replan'] and p4['previous']['ref'] == refs['P4']['ref']
    assert p6['available'] and not p6['needs_replan']
    assert plan2['executable'] == ['P6'] and plan2['replan'] == ['P4']
