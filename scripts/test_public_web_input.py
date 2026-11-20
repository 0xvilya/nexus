"""Bounded checks of public test forms using the actual Nexus browser tools.

Only harmless draft text is entered. No form is submitted, and each run uses
an isolated Chromium profile. Third-party pages may change after this test.
"""
import json
from pathlib import Path
import time
from uuid import uuid4

import bootstrap
from rainforge_recovery import atomic_json
from rainforge_worker.browser import ManagedBrowser


SITES = [
    {
        'url': 'https://www.selenium.dev/selenium/web/web-form.html',
        'fields': [('Text input', 'input'), ('Textarea', 'textarea')],
    },
    {
        'url': 'https://testpages.eviltester.com/pages/forms/html-form/',
        'fields': [('username', 'input'), ('comments', 'textarea')],
    },
]


def main():
    root = bootstrap.ROOT
    profile = root / 'work' / ('public-web-input-' + uuid4().hex[:10])
    profile.mkdir(parents=True)
    browser = ManagedBrowser(profile)
    proof = {'status': 'FAIL', 'profile': str(profile), 'submitted': False,
             'real_model_used': False, 'sites': []}
    try:
        for site in SITES:
            record = {'requested_url': site['url'], 'status': 'FAIL', 'fields': []}
            proof['sites'].append(record)
            started = time.monotonic()
            try:
                page = browser.navigate(site['url'])
                record['observed_url'] = page['url']
                record['title'] = page['title']
                for label, tag in site['fields']:
                    state = browser.observe()
                    candidates = [
                        item for item in state['elements']
                        if item['can_type'] and item['tag'] == tag
                        and label.casefold() in item['label'].casefold()
                    ]
                    if len(candidates) != 1:
                        record['observed_labels'] = [
                            item['label'] for item in state['elements']
                        ]
                        raise AssertionError(f'Expected one editable {label!r}, found {len(candidates)}')
                    item = candidates[0]
                    text = 'Nexus test draft'
                    result = browser.act_observed(state['token'], item['ref'], 'type', text)
                    assert result['acted'], result
                    fresh = browser.observe()
                    changed = next(
                        element for element in fresh['elements']
                        if element['label'] == item['label'] and element['tag'] == tag
                    )
                    assert changed['value'] == text, changed
                    record['fields'].append({'label': item['label'], 'tag': tag, 'value_verified': text})
                record['status'] = 'PASS'
            except (AssertionError, RuntimeError, ValueError, OSError) as error:
                record['error'] = str(error)[:700]
            record['elapsed_seconds'] = round(time.monotonic() - started, 3)
        if all(site['status'] == 'PASS' for site in proof['sites']):
            proof['status'] = 'PASS'
    finally:
        browser.close()
        proof['owned_browser_closed'] = browser.process is None
        atomic_json(root / 'work' / 'public-web-input-proof.json', proof)
        print(json.dumps(proof, ensure_ascii=True))
    if proof['status'] != 'PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()

