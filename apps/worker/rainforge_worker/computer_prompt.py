"""Bound model observations and keep the tool schema aligned with the prompt."""
import json
import re


def browser_controls(elements, goal, max_bytes=3700):
    words = {word for word in re.findall(r'\w+', goal.casefold()) if len(word) >= 3}

    def score(element):
        labels = [element.get('label') or ''] + [o.get('label') or '' for o in (element.get('options') or ()) if isinstance(o, dict)]
        return sum(word in ' '.join(labels).casefold() for word in words)

    controls = []
    size = 2
    for element in sorted(elements, key=score, reverse=True):
        if not (element.get('can_click') or element.get('can_type') or element.get('can_drag') or element.get('can_drop')):
            continue
        row = {key: element[key] for key in (
            'ref', 'label', 'tag', 'type', 'can_click', 'can_type', 'can_drag', 'can_drop', 'value', 'checked', 'pressed'
        ) if key in element}
        options = element.get('options')
        if options:
            row['options'] = sorted((o for o in options if isinstance(o, dict)), key=lambda o: (
                not o.get('disabled', False), sum(word in ((o.get('label') or '') + ' ' + (o.get('value') or '')).casefold()
                                                   for word in words)), reverse=True)[:10]
        cost = len(json.dumps(row, ensure_ascii=False).encode('utf-8')) + 2
        while cost + size > max_bytes and row.get('options'):
            row['options'].pop()
            cost = len(json.dumps(row, ensure_ascii=False).encode('utf-8')) + 2
        if cost + size <= max_bytes:
            controls.append(row)
            size += cost
        if len(controls) >= 20:
            break
    return controls


def check_plan_scope(action, schema):
    if not isinstance(action, dict) or not isinstance(action.get('tool'), str):
        raise ValueError('Invalid action')
    variants = schema['properties']['actions']['items']['oneOf']
    for variant in variants:
        fields = variant['properties']
        if fields['tool']['enum'] != [action['tool']]:
            continue
        if set(action) != set(variant['required']):
            raise ValueError('Missing or unexpected action fields')
        if any('enum' in spec and action[key] not in spec['enum'] for key, spec in fields.items()):
            raise ValueError('Choose a target shown in the current observation')
        return
    raise ValueError('Choose a tool available for this step')
