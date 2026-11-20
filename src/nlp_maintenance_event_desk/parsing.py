"""从技术员的中文交接记录中抽取设备指代、停机、试运行和签认。

解析只产出带不确定性的观察（Observation）：设备指代可以是多个候选，
时间可以是精确时刻，也可以是“10:40 之后”“上一次复位前”这类约束。
所有观察都保留原记录编号和原文片段，供经办人回查，不替代原始来源。
"""

import re
from dataclasses import dataclass, field
from datetime import datetime

TRIAL_WORDS = ('复位', '试跑', '跑过', '试运行')
SIGNOFF_WORDS = ('签认', '检查合格', '复检合格')
STOP_WORD = '停'
CORRECTION_MARK = '复核'
LATEST_TRIAL_BEFORE = '上一次复位前'

MACHINE_RE = re.compile(r'[A-Za-z]\d+')
TIME_RE = re.compile(r'(\d{1,2}):(\d{2})')
AFTER_RE = re.compile(r'(\d{1,2}:\d{2})\s*之后')
INSPECTOR_RE = re.compile(r'检查员\s*([一-鿿]{1,4})')
NAME_RE = re.compile(r'([一-鿿]{1,3}工)')
CLAUSE_SPLIT = re.compile(r'[。；;！!？?\n]')


@dataclass
class Observation:
    """一次停机、试运行或签认的观察，可能来自已核对事件或中文记录。"""

    obs_id: str
    kind: str  # 停机 / 试运行 / 签认
    machines: list  # 设备指代候选，单一元素表示已确定
    exact: datetime | None = None
    not_before: datetime | None = None
    not_after: datetime | None = None
    before_obs: tuple = ()  # 本观察必须先于这些观察
    after_obs: tuple = ()  # 本观察必须晚于这些观察
    inspector: str | None = None
    shift: str | None = None
    record_ids: list = field(default_factory=list)
    excerpts: list = field(default_factory=list)
    verified: bool = False  # 已核对的技术记录（结构化事件）
    before_latest_trial: bool = False  # “上一次复位前”锚点，稍后解析


@dataclass
class Correction:
    """复核类记录：把先前某条模糊观察的设备指代钉到一台设备上。"""

    kind: str | None
    exact: datetime | None
    machine: str | None
    record_id: str
    excerpt: str


def _combine_date(registry, hour, minute):
    date = registry.get('date')
    if not date:
        return None
    day = datetime.fromisoformat(date).date()
    return datetime(day.year, day.month, day.day, int(hour), int(minute))


def _kinds_in(clause):
    """按叙述次序列出分句中的事件类型，每个类型只取第一次提及。"""
    hits = []
    for match in re.finditer(STOP_WORD, clause):
        hits.append((match.start(), match.end(), '停机'))
    for word in TRIAL_WORDS:
        pos = clause.find(word)
        if pos >= 0:
            hits.append((pos, pos + len(word), '试运行'))
    for word in SIGNOFF_WORDS:
        pos = clause.find(word)
        if pos >= 0:
            hits.append((pos, pos + len(word), '签认'))
    hits.sort()
    seen = set()
    ordered = []
    for start, end, kind in hits:
        if kind in seen:
            continue
        seen.add(kind)
        ordered.append((start, end, kind))
    return ordered


def _machines_in(text, known, areas):
    explicit = sorted({m.upper() for m in MACHINE_RE.findall(text)} & known)
    if explicit:
        return explicit
    found = []
    for area, ids in areas.items():
        if area and area in text:
            found.extend(ids)
    return sorted(set(found))


def _inspector_in(text):
    match = INSPECTOR_RE.search(text) or NAME_RE.search(text)
    return match.group(1) if match else None


def parse_records(records, registry):
    """把中文记录解析为观察、复核钉正和警告。记录到达次序不影响结果。"""
    known = {m['id'] for m in registry['machines']}
    areas = {}
    for item in registry['machines']:
        areas.setdefault(item.get('area'), []).append(item['id'])
    observations, corrections, warnings = [], [], []
    for record in records:
        rid = record['record_id']
        record_obs = []
        clauses = [c.strip() for c in CLAUSE_SPLIT.split(record['text']) if c.strip()]
        for clause in clauses:
            if clause.startswith(CORRECTION_MARK):
                corrections.append(_parse_correction(clause, rid, registry))
                continue
            anchored = LATEST_TRIAL_BEFORE in clause
            masked = clause.replace(LATEST_TRIAL_BEFORE, '此前')
            kinds = _kinds_in(masked)
            if not kinds:
                continue
            times = list(TIME_RE.finditer(masked))
            after = AFTER_RE.search(masked)
            not_before = None
            if after:
                hour, minute = after.group(1).split(':')
                not_before = _combine_date(registry, hour, minute)
            machines = _machines_in(masked, known, areas)
            inspector = _inspector_in(masked)
            made = []
            for index, (start, _end, kind) in enumerate(kinds):
                # 时间只系到其之前最近的一个事件提及上
                span_start = kinds[index - 1][1] if index else 0
                exact = None
                for hit in times:
                    if span_start <= hit.start() < start:
                        exact = _combine_date(registry, hit.group(1), hit.group(2))
                obs = Observation(
                    obs_id=f'{rid}#{len(record_obs) + 1}',
                    kind=kind,
                    machines=list(machines),
                    exact=exact,
                    not_before=not_before,
                    inspector=inspector,
                    shift=registry.get('shift'),
                    record_ids=[rid],
                    excerpts=[clause],
                    before_latest_trial=anchored,
                )
                if made:
                    made[-1].before_obs += (obs.obs_id,)  # 同一句内按叙述先后排序
                made.append(obs)
                record_obs.append(obs)
        if len(record_obs) == 1:
            _inherit_record_level(record_obs[0], record['text'], known, areas, registry)
        for obs in record_obs:
            if not obs.machines:
                obs.machines = sorted(known)
                warnings.append(f'{rid}: 未能定位设备指代，按全部登记设备候选处理')
        observations.extend(record_obs)
    return observations, corrections, warnings


def _inherit_record_level(obs, text, known, areas, registry):
    """整段记录只产出一个观察时，记录级的时间、检验人和设备可以继承。"""
    if not obs.machines:
        obs.machines = _machines_in(text, known, areas)
    if obs.exact is None:
        times = list(TIME_RE.finditer(text))
        if len(times) == 1:
            obs.exact = _combine_date(registry, times[0].group(1), times[0].group(2))
    if obs.inspector is None:
        obs.inspector = _inspector_in(text)


def _parse_correction(clause, record_id, registry):
    machine = MACHINE_RE.search(clause)
    moment = TIME_RE.search(clause)
    kind = None
    if STOP_WORD in clause:
        kind = '停机'
    elif any(word in clause for word in TRIAL_WORDS):
        kind = '试运行'
    elif any(word in clause for word in SIGNOFF_WORDS) or '合格' in clause:
        kind = '签认'
    exact = None
    if moment:
        exact = _combine_date(registry, moment.group(1), moment.group(2))
    return Correction(kind, exact, machine.group(0).upper() if machine else None, record_id, clause)


def apply_corrections(observations, corrections):
    """复核钉正只作用于仍有多个设备候选的观察，不改写已核对事件。"""
    warnings = []
    for correction in corrections:
        matches = [
            obs
            for obs in observations
            if len(obs.machines) > 1
            and (correction.kind is None or obs.kind == correction.kind)
            and (correction.exact is None or obs.exact == correction.exact)
        ]
        if not matches:
            warnings.append(f'{correction.record_id}: 复核“{correction.excerpt}”未匹配到待确认观察')
            continue
        for obs in matches:
            if correction.machine and correction.machine not in obs.machines:
                warnings.append(
                    f'{correction.record_id}: 复核设备 {correction.machine} 不在原候选 {obs.machines} 中，仍按复核钉正'
                )
            if correction.machine:
                obs.machines = [correction.machine]
    return warnings


def structured_observations(events):
    """已核对的技术记录：设备、时刻和类型都已确定，视为无歧义观察。"""
    result = []
    for event in events:
        result.append(
            Observation(
                obs_id=event['id'],
                kind=event['kind'],
                machines=[event['machine']],
                exact=datetime.fromisoformat(event['occurred']),
                inspector=event.get('inspector'),
                shift=event.get('shift'),
                verified=True,
            )
        )
    return result


def merge_observations(observations):
    """同一设备、同一类型、同一精确时刻的观察视为同一事件的多处来源。"""
    merged = []
    for obs in observations:
        twin = None
        if obs.exact is not None and len(obs.machines) == 1:
            for other in merged:
                if (
                    other.exact == obs.exact
                    and other.kind == obs.kind
                    and other.machines == obs.machines
                ):
                    twin = other
                    break
        if twin is None:
            merged.append(obs)
            continue
        twin.record_ids = sorted(set(twin.record_ids) | set(obs.record_ids))
        twin.excerpts.extend(obs.excerpts)
        twin.before_obs = tuple(set(twin.before_obs) | set(obs.before_obs))
        twin.after_obs = tuple(set(twin.after_obs) | set(obs.after_obs))
        twin.inspector = twin.inspector or obs.inspector
        twin.verified = twin.verified or obs.verified
    return merged


def resolve_trial_anchors(observations):
    """把“上一次复位前”解析为对最近一次试运行观察的先后约束。"""
    trials = [obs for obs in observations if obs.kind == '试运行']
    warnings = []
    timed = [obs for obs in trials if obs.exact is not None]
    target = None
    if timed:
        target = max(timed, key=lambda obs: obs.exact)
    elif trials:
        target = trials[-1]
    for obs in observations:
        if not obs.before_latest_trial:
            continue
        if target is None:
            warnings.append(f'{obs.obs_id}: 记录提到“上一次复位前”，但没有可对应的试运行')
            continue
        obs.before_obs += (target.obs_id,)
    return warnings
