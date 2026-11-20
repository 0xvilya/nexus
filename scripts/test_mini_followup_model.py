"""Verify Mini's voice RPC with a real local model, without capturing a microphone."""
import argparse
import ast
import json
import tempfile
import time
from pathlib import Path

import bootstrap
from rainforge_worker.rpc import Worker
from rainforge_recovery import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models-dir', required=True, type=Path)
    args = parser.parse_args()
    root = bootstrap.ROOT
    model = 'qwen2.5-coder-1.5b-instruct-q4_k_m.gguf'
    if not (args.models_dir / model).is_file():
        parser.error('Qwen Coder 1.5B must already be installed')
    work = root / 'work'
    work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mini-followup-', dir=work) as folder:
        worker = Worker(Path(folder), root / 'runtime', models_dir=args.models_dir)
        try:
            def ask(goal, mode):
                result = worker.dispatch('task.start', {
                    'goal': goal, 'mode': mode, 'conversation_id': conversation,
                    'model_filename': model,
                })
                worker.actions.thread.join(180)
                assert not worker.actions.thread.is_alive(), 'Task timed out'
                return worker.dispatch('task.status', {'run_id': result['id']})

            conversation = worker.dispatch('conversation.create', {})['id']
            identity = ask('Кто ты?', 'voice')
            assert identity['tool_calls'] == identity['planner_calls'] == 0, identity
            goal = 'Напиши компактную программу на Python в файле greet.py, которая выводит приветствие Hello Nexus'
            refusal = ask(goal, 'chat')
            assert refusal['route'] == 'chat' and refusal['tool_calls'] == 0, refusal
            started = time.monotonic()
            created = ask('Так ты сам сделаю тебе есть инструменты для этого.', 'voice')
            assert created['effective_goal'] == goal and created['state'] == 'file_created', created
            assert created['tool_calls'] == 1, created
            saved = created['results'][0]
            source = worker.workspace_tools.read(saved['path'])
            assert saved['sha256'] == source['sha256']
            path = worker.workspace_tools.path(saved['path'])
            tree = ast.parse(path.read_text(encoding='utf-8'))
            compile(tree, saved['path'], 'exec')
            assert any(isinstance(node, ast.Constant) and node.value == 'Hello Nexus' for node in ast.walk(tree))
            replay = ask('Сделай сам', 'voice')
            assert replay['route'] == 'chat' and replay['tool_calls'] == 0
            proof = {
                'status': 'PASS', 'model': model, 'identity_tools': 0,
                'followup_tools': 1, 'created_file': saved['path'],
                'syntax_verified': True, 'greeting_literal_verified': True,
                'replay_tools': 0, 'elapsed_seconds': round(time.monotonic() - started, 2),
            }
            atomic_json(work / 'mini-followup-model-proof.json', proof)
            print(json.dumps(proof, ensure_ascii=False))
        finally:
            worker.close()


if __name__ == '__main__':
    main()
