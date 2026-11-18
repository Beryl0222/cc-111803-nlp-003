from collections import defaultdict
from datetime import datetime


class IncidentHistory:
    def __init__(self, machines, events):
        self.machines = {item['id'] for item in machines}
        self.events = defaultdict(list)
        ids = set()
        for event in events:
            if event['id'] in ids or event['machine'] not in self.machines:
                raise ValueError('事件编号重复或设备未登记')
            if event['kind'] not in {'停机', '试运行', '签认'}:
                raise ValueError('事件类型不受支持')
            ids.add(event['id'])
            self.events[event['machine']].append((datetime.fromisoformat(event['occurred']), event))

    def latest_episode(self, machine):
        sequence = sorted(self.events[machine], key=lambda item: (item[0], item[1]['id']))
        episode = []
        for _, event in sequence:
            if event['kind'] == '停机':
                episode = [event]
            elif episode:
                episode.append(event)
        return episode


class RecoveryReview:
    def __init__(self, roster):
        self.roster = roster

    def clearance(self, episode):
        if not episode:
            return '未停机', None
        stop = episode[0]
        trial = False
        for event in episode[1:]:
            if event['incident'] != stop['id']:
                continue
            if event['kind'] == '试运行':
                trial = True
            elif trial and event['inspector'] in self.roster.get(event['shift'], ()):
                return '已签认恢复', event['id']
        return '待恢复', None


class ShiftPlanner:
    def __init__(self, incidents, review, released):
        self.incidents = incidents
        self.review = review
        self.released = {item['machine']: item for item in released}

    def current_plan(self):
        rows = []
        for machine in sorted(self.incidents.machines):
            episode = self.incidents.latest_episode(machine)
            status, clearance = self.review.clearance(episode)
            previous = self.released.get(machine)
            rows.append({'machine': machine, 'status': status, 'clearance': clearance,
                         'available': status != '待恢复', 'previous': previous,
                         'needs_replan': previous is not None and previous['released'] and status == '待恢复'})
        return rows
