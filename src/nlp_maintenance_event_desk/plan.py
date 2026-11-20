"""面向班长的排产计划与交接单。

计划只采用现在有依据的确认结果：全部可信解释都支持当前一次停机已解除
的设备才可排产。已签发的交接单持久保存，看得见当时采用的设备和事件；
晚到的记录若使已交班设备进入待核，计划会标出需要重新排产的设备、仍可
执行的安排，以及需要维护人员确认的记录段。
"""

import json
from datetime import datetime
from pathlib import Path

from .interpretation import analyze_machines


def build_plan(observations, registry, released, warnings=()):
    """汇总每台设备的排产资格，并对照此前签发的交接单。"""
    machines = [item['id'] for item in registry['machines']]
    analyses = analyze_machines(observations, machines, registry['roster'])
    previous = {}
    for sheet in released:
        previous[sheet['machine']] = sheet  # 同一设备以最后签发的为准
    rows = []
    for machine in sorted(machines):
        analysis = analyses[machine]
        sheet = previous.get(machine)
        was_available = bool(sheet) and (
            sheet.get('released') or sheet.get('available') or sheet.get('status') in ('已签认恢复', '未停机')
        )
        rows.append(
            {
                'machine': machine,
                'status': analysis.status,
                'available': analysis.available,
                'clearance': analysis.options[0]['signoff'] if len(analysis.options) == 1 else None,
                'clearance_options': analysis.options,
                'causes': analysis.causes,
                'pending_confirmation': analysis.pending,
                'rejected_signoffs': analysis.rejected,
                'events': analysis.events,
                'interpretations': analysis.interpretations,
                'previous': sheet,
                'needs_replan': bool(was_available) and not analysis.available,
            }
        )
    return {
        'shift': registry['shift'],
        'date': registry.get('date'),
        'executable': [row['machine'] for row in rows if row['available']],
        'held': [row['machine'] for row in rows if row['status'] == '待核'],
        'replan': [row['machine'] for row in rows if row['needs_replan']],
        'machines': rows,
        'warnings': list(warnings),
    }


def load_sheets(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return []
    if not isinstance(value, list):
        raise ValueError('交接单存档必须是列表')
    return value


def _next_ref(existing):
    highest = 0
    for sheet in existing:
        ref = sheet.get('ref', '')
        if ref.startswith('handover-') and ref[len('handover-') :].isdigit():
            highest = max(highest, int(ref[len('handover-') :]))
    return f'handover-{highest + 1}'


def issue_sheets(plan, observations, released, store_path):
    """为当前可排产的设备签发交接单；结论未变的设备不重复签发。"""
    records_by_event = {}
    for obs in observations:
        records_by_event[obs.obs_id] = list(obs.record_ids)
    stored = load_sheets(store_path)
    existing = list(released) + list(stored)  # 历史种子与已签发只用于编号
    issued, kept = [], []
    for row in plan['machines']:
        if not row['available']:
            continue
        latest = row['previous']
        if (
            latest
            and latest.get('status') == row['status']
            and latest.get('clearance') == row['clearance']
            and latest.get('events') == row['events']
        ):
            kept.append(latest['ref'])
            continue
        records = sorted({rid for event in row['events'] for rid in records_by_event.get(event, [])})
        sheet = {
            'ref': _next_ref(existing),
            'issued_at': datetime.now().isoformat(timespec='seconds'),
            'shift': plan['shift'],
            'machine': row['machine'],
            'status': row['status'],
            'available': True,
            'clearance': row['clearance'],
            'clearance_options': row['clearance_options'],
            'events': row['events'],
            'records': records,
        }
        existing.append(sheet)
        issued.append(sheet)
    if issued:
        Path(store_path).parent.mkdir(parents=True, exist_ok=True)
        Path(store_path).write_text(
            json.dumps(stored + issued, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
        )
    return {'issued': issued, 'kept': kept}
