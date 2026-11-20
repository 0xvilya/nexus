import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import bootstrap
from rainforge_worker.browser import ManagedBrowser


@unittest.skipUnless(os.getenv('RF_BROWSER_TEST') == '1', 'Real browser fixture requires RF_BROWSER_TEST=1')
class EditorTests(unittest.TestCase):
    def test_editor_input_is_plain_text_and_obeys_current_capabilities(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            browser = ManagedBrowser(Path(tmp))
            try:
                html = '''<div id="editor" contenteditable="true" role="textbox" aria-label="Article draft">Old <b>draft</b></div>
                <div id="readonly" contenteditable="true" aria-readonly="true" aria-label="Read only">Keep me</div>
                <div id="disabled" contenteditable="true" aria-disabled="true" aria-label="Disabled editor">Keep</div>
                <div contenteditable="false" aria-label="Not editable">Keep</div>
                <div id="cancelled" contenteditable="true" aria-label="Protected editor">Original</div>
                <div id="focuschange" contenteditable="true" aria-label="Changing editor">Original</div><div id="large" contenteditable="true" aria-label="Large editor"></div><div contenteditable="true" aria-label="Editor with locked content"><span contenteditable="false">Immutable</span></div>'''
                browser.evaluate('document.body.innerHTML=' + json.dumps(html))
                browser.evaluate("large.textContent='x'.repeat(2001);focuschange.addEventListener('beforeinput',()=>{focuschange.textContent='Changed by page'});editor.addEventListener('input',e=>document.body.dataset.lastInput=e.inputType);cancelled.addEventListener('beforeinput',e=>e.preventDefault())")
                state = browser.observe()
                elements = {row['label']: row for row in state['elements']}
                self.assertTrue(elements['Article draft']['can_type'])
                self.assertEqual(elements['Article draft']['value'], 'Old draft')
                self.assertFalse(elements['Read only']['can_type'])
                self.assertFalse(elements['Disabled editor']['can_type'])
                self.assertFalse(elements['Large editor']['can_type'])
                self.assertFalse(elements['Editor with locked content']['can_type'])
                value = '<img src=x onerror=alert(1)>Привет\nline 2'
                result = browser.act_observed(state['token'], elements['Article draft']['ref'], 'type', value)
                self.assertTrue(result['acted'])
                self.assertEqual(browser.evaluate('editor.textContent'), value)
                self.assertEqual(browser.evaluate('editor.querySelectorAll("img").length'), 0)
                self.assertEqual(browser.evaluate('document.body.dataset.lastInput'), 'insertText')
                state = browser.observe()
                row = next(row for row in state['elements'] if row['label'] == 'Article draft')
                browser.evaluate("editor.setAttribute('aria-readonly','true')")
                self.assertEqual(browser.act_observed(state['token'], row['ref'], 'type', 'Unsafe')['reason'], 'stale_observation')
                state = browser.observe()
                row = next(row for row in state['elements'] if row['label'] == 'Protected editor')
                self.assertEqual(browser.act_observed(state['token'], row['ref'], 'type', 'Changed')['reason'], 'input_cancelled')
                self.assertEqual(browser.evaluate('cancelled.textContent'), 'Original')
                state = browser.observe()
                row = next(row for row in state['elements'] if row['label'] == 'Changing editor')
                self.assertEqual(browser.act_observed(state['token'], row['ref'], 'type', 'Overwrite')['reason'], 'stale_observation')
                self.assertEqual(browser.evaluate('focuschange.textContent'), 'Changed by page')
            finally:
                browser.close()

    def test_form_fields_remain_observable_after_large_navigation(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            browser = ManagedBrowser(Path(tmp))
            try:
                navigation = ''.join('<a href="#">Navigation '+str(index)+'</a>' for index in range(150))
                html = navigation + '<textarea aria-label="Comments" id="comments"></textarea><button>Preview</button>'
                browser.evaluate('document.body.innerHTML=' + json.dumps(html))
                state = browser.observe()
                self.assertEqual(len(state['elements']), 80)
                field = next(row for row in state['elements'] if row['label'] == 'Comments')
                self.assertTrue(field['can_type'])
                self.assertTrue(any(row['label'] == 'Preview' for row in state['elements']))
                self.assertTrue(browser.act_observed(state['token'], field['ref'], 'type', 'Nexus test')['acted'])
                self.assertEqual(browser.evaluate('comments.value'), 'Nexus test')
            finally:
                browser.close()

    def test_spa_transition_invalidates_old_nodes_and_preserves_new_field_events(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            browser = ManagedBrowser(Path(tmp))
            try:
                browser.evaluate('document.body.innerHTML=' + json.dumps('<input aria-label="Name" id="name"><button id="next">Continue</button><div id="step"></div>'))
                browser.evaluate("next.onclick=()=>{step.innerHTML='<textarea aria-label=Description id=description></textarea><button id=done>Confirm</button><p id=result></p>';done.onclick=()=>{result.textContent='READY '+description.value};document.getElementById('name').remove();next.remove()}")
                state = browser.observe()
                original = next(row for row in state['elements'] if row['label'] == 'Name')
                browser.act_observed(state['token'], original['ref'], 'type', 'Nexus')
                state = browser.observe()
                button = next(row for row in state['elements'] if row['label'] == 'Continue')
                browser.act_observed(state['token'], button['ref'], 'click')
                self.assertFalse(browser.act_observed(state['token'], original['ref'], 'type', 'Stale')['acted'])
                state = browser.observe()
                field = next(row for row in state['elements'] if row['label'] == 'Description')
                browser.act_observed(state['token'], field['ref'], 'type', 'New page')
                state = browser.observe()
                button = next(row for row in state['elements'] if row['label'] == 'Confirm')
                browser.act_observed(state['token'], button['ref'], 'click')
                self.assertTrue(browser.wait_for('#result', 'READY New page', 2)['verified'])
            finally:
                browser.close()

