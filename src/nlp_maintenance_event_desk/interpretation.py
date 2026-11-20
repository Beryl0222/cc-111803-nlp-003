"""在不确定性下核对停机、试运行和签认。

记录到达次序与事件次序可以不同，时间也未必精确到分钟。这里把每条
观察的设备归属（多候选时）和每台设备上满足时间约束的事件次序都枚举
为可信解释。排产只有在现有可信解释都支持当前一次停机已解除时才继续；
解释之间结论不一致时，区分不确定来自设备身份还是试运行与签认的先后。
"""

from dataclasses import dataclass, field
from itertools import permutations, product

MAX_EVENTS_PER_MACHINE = 8  # 超出则拒绝猜测，交人工核对
MAX_ASSIGNMENTS = 256


def must_precede(first, second):
    """根据时间约束判断 first 是否一定先于 second。"""
    if second.obs_id in first.before_obs or first.obs_id in second.after_obs:
        return True
    if first.exact and second.exact:
        return first.exact < second.exact
    if first.exact and second.not_before:
        return first.exact <= second.not_before
    if first.not_after and second.exact:
        return first.not_after <= second.exact
    if first.not_after and second.not_before:
        return first.not_after <= second.not_before
    return False


def credible_orders(observations):
    """枚举一台设备上与全部时间约束一致的事件次序。"""
    if len(observations) > MAX_EVENTS_PER_MACHINE:
        raise ValueError('单台设备待核对事件过多，解释空间过大，请先人工核对记录')
    orders = []
    for perm in permutations(observations):
        position = {id(obs): index for index, obs in enumerate(perm)}
        consistent = all(
            position[id(a)] < position[id(b)]
            for a in observations
            for b in observations
            if a is not b and must_precede(a, b)
        )
        if consistent:
            orders.append(perm)
    return orders


def evaluate_episode(order, roster):
    """沿一个事件次序核对最新一段停机：停机后须先试运行，再由当班检验人签认。"""
    stopped = False
    trial = None
    basis = None
    for obs in order:
        if obs.kind == '停机':
            stopped, trial, basis = True, None, None
        elif obs.kind == '试运行':
            if stopped and basis is None:
                trial = obs
        elif obs.kind == '签认':
            if stopped and basis is None and trial is not None:
                authorized = roster.get(obs.shift or '', ())
                if obs.inspector and obs.inspector in authorized:
                    basis = (trial, obs)
    available = (not stopped) or basis is not None
    return {'available': available, 'stopped': stopped, 'basis': basis}


@dataclass
class MachineAnalysis:
    machine: str
    status: str
    available: bool
    events: list = field(default_factory=list)
    interpretations: int = 0
    options: list = field(default_factory=list)  # 全部可信的签认依据，供回查
    causes: list = field(default_factory=list)  # 待核原因：设备身份 / 试运行与签认先后
    pending: list = field(default_factory=list)  # 需要维护人员确认的记录段
    rejected: list = field(default_factory=list)  # 未采信的签认及原因


def _assignments(observations):
    ambiguous = [obs for obs in observations if len(obs.machines) > 1]
    total = 1
    for obs in ambiguous:
        total *= len(obs.machines)
        if total > MAX_ASSIGNMENTS:
            raise ValueError('设备指代组合过多，解释空间过大，请先人工核对记录')
    keys = [obs.obs_id for obs in ambiguous]
    for choice in product(*(obs.machines for obs in ambiguous)):
        yield dict(zip(keys, choice))


def _option(trial, signoff):
    records = sorted(set(trial.record_ids) | set(signoff.record_ids))
    return {
        'trial': trial.obs_id,
        'signoff': signoff.obs_id,
        'records': records,
        'excerpts': list(trial.excerpts) + list(signoff.excerpts),
    }


def _segment(obs, question):
    return {
        'record': obs.record_ids[0] if obs.record_ids else obs.obs_id,
        'excerpt': obs.excerpts[0] if obs.excerpts else '',
        'question': question,
    }


def iter_interpretations(observations, machines, roster):
    """按设备归属枚举可信解释；每个解释给出各设备全部可信次序的核对结果。"""
    for assignment in _assignments(observations):
        owner = {obs.obs_id: assignment.get(obs.obs_id, obs.machines[0]) for obs in observations}
        per_machine = {}
        for machine in machines:
            own = [obs for obs in observations if owner[obs.obs_id] == machine]
            orders = credible_orders(own)
            if not orders:
                per_machine = None
                break  # 时间约束自相矛盾的解释不可信
            per_machine[machine] = [(order, evaluate_episode(order, roster)) for order in orders]
        if per_machine is not None:
            yield assignment, per_machine


def analyze_machines(observations, machines, roster):
    """对每台设备汇总全部可信解释，得出排产资格和待核事项。"""
    per_machine = {machine: [] for machine in machines}
    for assignment, verdicts_by_machine in iter_interpretations(observations, machines, roster):
        key = tuple(sorted(assignment.items()))
        for machine in machines:
            own = [obs for obs in observations if assignment.get(obs.obs_id, obs.machines[0]) == machine]
            per_machine[machine].append({'key': key, 'obs': own, 'verdicts': verdicts_by_machine[machine]})
    return {machine: _aggregate(machine, per_machine[machine], roster) for machine in machines}


def _aggregate(machine, entries, roster):
    flat = [verdict for entry in entries for _, verdict in entry['verdicts']]
    events = sorted({obs.obs_id for entry in entries for obs in entry['obs']})
    if not entries or not flat:
        return MachineAnalysis(
            machine=machine,
            status='待核',
            available=False,
            events=events,
            causes=['记录相互矛盾'],
        )
    all_available = all(item['available'] for item in flat)
    any_available = any(item['available'] for item in flat)
    ever_stopped = any(obs.kind == '停机' for entry in entries for obs in entry['obs'])
    if all_available:
        status = '已签认恢复' if ever_stopped else '未停机'
    elif any_available:
        status = '待核'
    else:
        status = '待恢复'
    analysis = MachineAnalysis(
        machine=machine,
        status=status,
        available=all_available,
        events=events,
        interpretations=len(flat),
    )
    bases = {}
    for _, verdict in (v for entry in entries for v in entry['verdicts']):
        if verdict['basis']:
            trial, signoff = verdict['basis']
            bases[(trial.obs_id, signoff.obs_id)] = _option(trial, signoff)
    analysis.options = [bases[key] for key in sorted(bases)]
    if status == '待核':
        analysis.causes = _causes(entries)
        analysis.pending = _pending(entries)
    analysis.rejected = _rejected(entries, roster)
    return analysis


def _causes(entries):
    causes = []
    # 同一设备归属下事件次序不同就改变结论：不确定来自试运行与签认的先后
    for entry in entries:
        outcomes = {verdict['available'] for _, verdict in entry['verdicts']}
        if len(outcomes) > 1:
            causes.append('试运行与签认先后')
            break
    # 设备归属不同的解释给出不同结论：不确定来自设备身份
    conclusions = {}
    for entry in entries:
        obs_ids = tuple(sorted(obs.obs_id for obs in entry['obs']))
        concl = all(verdict['available'] for _, verdict in entry['verdicts'])
        conclusions.setdefault(obs_ids, set()).add(concl)
    distinct = {ids: any(values) for ids, values in conclusions.items()}
    if len(set(distinct.values())) > 1:
        causes.insert(0, '设备身份')
    return causes


def _pending(entries):
    segments = {}
    # 设备身份待核：列出归属在不同解释间变化的模糊观察
    ownership = {}
    for entry in entries:
        for obs in entry['obs']:
            if len(obs.machines) > 1:
                ownership.setdefault(obs.obs_id, {'obs': obs, 'count': 0})
                ownership[obs.obs_id]['count'] += 1
    total = len({entry['key'] for entry in entries})
    for item in ownership.values():
        obs = item['obs']
        if item['count'] < total and obs.record_ids:
            question = f"请维护人员确认该{obs.kind}属于哪台设备（候选：{'/'.join(obs.machines)}）"
            segments[obs.obs_id] = _segment(obs, question)
    # 先后待核：列出交换先后会改变结论的事件对
    for entry in entries:
        outcomes = {verdict['available'] for _, verdict in entry['verdicts']}
        if len(outcomes) == 1:
            continue
        obs_list = entry['obs']
        for i, first in enumerate(obs_list):
            for second in obs_list[i + 1 :]:
                if first.kind == second.kind:
                    continue
                orientations = {}
                for order, verdict in entry['verdicts']:
                    seq = [obs.obs_id for obs in order]
                    forward = seq.index(first.obs_id) < seq.index(second.obs_id)
                    orientations.setdefault(forward, set()).add(verdict['available'])
                if len(orientations) == 2 and orientations[True] != orientations[False]:
                    for obs, other in ((first, second), (second, first)):
                        if obs.record_ids and obs.obs_id not in segments:
                            question = (
                                f'请维护人员确认该{obs.kind}与{other.kind}（{other.obs_id}）的先后，'
                                '以判定对应哪一段停机'
                            )
                            segments[obs.obs_id] = _segment(obs, question)
    return [segments[key] for key in sorted(segments)]


def _rejected(entries, roster):
    """列出不可能采信的签认：检验人不在名册，或先后关系永远不成立。

    只是晚于其他有效签认、因而未成为依据的签认不算未采信。
    """
    rejected = {}
    for entry in entries:
        for obs in entry['obs']:
            if obs.kind != '签认' or obs.obs_id in rejected:
                continue
            if not obs.inspector:
                reason = '未登记检验人'
            elif obs.inspector not in roster.get(obs.shift or '', ()):
                reason = '检验人不在当班名册'
            elif not _clearable(obs, entry['verdicts']):
                reason = '签认与本次停机、试运行的先后关系不成立'
            else:
                continue
            rejected[obs.obs_id] = {
                'signoff': obs.obs_id,
                'records': list(obs.record_ids),
                'excerpts': list(obs.excerpts),
                'reason': reason,
            }
    return [rejected[key] for key in sorted(rejected)]


def _clearable(signoff, verdicts):
    """是否存在可信次序，使该签认出现时已有停机且其后有试运行。"""
    for order, _ in verdicts:
        stopped = False
        trial = None
        for obs in order:
            if obs.kind == '停机':
                stopped, trial = True, None
            elif obs.kind == '试运行':
                if stopped:
                    trial = obs
            elif obs is signoff:
                if stopped and trial is not None:
                    return True
                break
    return False
