import json
from pathlib import Path

def load_facts(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def summarize(facts):
    authorized = set(facts['roster'][facts['shift']])
    return [receipt['id'] for receipt in facts['receipts'] if receipt['inspector'] in authorized]
