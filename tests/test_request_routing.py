"""Regression coverage for routing through the worker's public task RPC."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bootstrap
from rainforge_worker.rpc import Worker


class RequestRoutingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        with patch("rainforge_worker.rpc.detect_gpu", return_value=[]):
            self.worker = Worker(Path(directory.name), bootstrap.ROOT / "runtime")
        self.addCleanup(self.worker.close)
        self.worker.runtime = MagicMock()
        self.worker.runtime.status.return_value = {"state": "ready", "model": "coder.gguf"}
        self.worker.browser = MagicMock()
        self.worker.browser.target = None
        self.worker.desktop.current = None
        self.worker.browser.navigate.return_value = {
            "url": "https://example.org/",
            "title": "Observed page",
            "text": "Fixture evidence",
        }
        self.engine = self.worker.actions
        self.thread = self.worker.dispatch("conversation.create", {})["id"]

    def run_turn(self, goal, **options):
        started = self.worker.dispatch("task.start", {
            "goal": goal, "mode": "auto", "conversation_id": self.thread, **options,
        })
        self.engine.thread.join(timeout=5)
        self.assertFalse(self.engine.thread.is_alive(), "The task did not reach a terminal state")
        self.assertEqual(started["conversation_id"], self.thread)
        status = self.worker.dispatch("task.status", {"run_id": started["id"]})
        history = self.worker.dispatch("conversation.read", {"conversation_id": self.thread})
        self.assertEqual(history["messages"][-1]["seq"], started["turn_seq"])
        self.assertEqual(history["messages"][-2]["text"], goal)
        self.assertNotEqual(history["messages"][-1]["state"], "running")
        return status, history["messages"][-1]

    def test_retry_after_failed_coding_turn_uses_original_request(self):
        from rainforge_worker.actions import PYTHON_UNWRITTEN
        goal = 'Напиши игру змейку на Python и сохрани в файл'
        plan = {'actions':[{'tool':'create_python','path':'snake.py'}]}
        with patch.object(self.engine, 'model_plan', return_value=plan), patch.object(self.engine, 'create_python', side_effect=ValueError(PYTHON_UNWRITTEN)):
            failed, _ = self.run_turn(goal)
        self.assertEqual(failed['state'], 'failed')
        with patch.object(self.engine, 'model_plan', return_value=plan), patch.object(self.engine, 'create_python', return_value={'path':'snake.py','syntax_verified':True}) as create:
            status, _ = self.run_turn('делай')
        self.assertEqual(status['effective_goal'], goal)
        self.assertNotEqual(status['route'], 'chat')
        self.assertTrue(create.called)
        self.assertEqual(create.call_args.args[0], goal)

    def test_spoken_screenshot_followup_recovers_coding_request(self):
        goal = 'Напиши на Python файл greet.py, который выводит приветствие'
        self.run_turn(goal, mode='chat')
        with patch.object(self.engine, 'model_plan', return_value={'actions':[
            {'tool':'create_python','path':'greet.py'},
        ]}), patch.object(self.engine, 'create_python', return_value={'path':'greet.py','syntax_verified':True}) as create:
            status, _ = self.run_turn('Так ты сам сделаю тебе есть инструменты для этого.', mode='voice')
            self.assertEqual(status['effective_goal'], goal)
            self.assertEqual(status['state'], 'file_created')
            self.assertEqual(status['tool_calls'], 1)
            create.assert_called_once()
            status, _ = self.run_turn('Сделай сам', mode='voice')
            self.assertEqual((status['route'], status['tool_calls']), ('chat',0))
            create.assert_called_once()

    def test_garbled_prior_request_asks_what_to_create(self):
        goal = 'Привет, сделаю мейку на моем рабочем столе, напает они написано'
        self.worker.runtime.generate.side_effect = lambda prompt, emit, *args, **kwargs: emit('Уточните.')
        self.run_turn(goal, mode='chat')
        with patch.object(self.engine, 'model_plan') as planner:
            status, _ = self.run_turn('Так ты сам сделаю тебе есть инструменты для этого.', mode='voice')
        self.assertEqual(status['route'], 'chat')
        self.assertIn('Уточните, что нужно сделать',status['message'])
        self.assertEqual(status['tool_calls'], 0)
        planner.assert_not_called()

    def test_retry_does_not_replay_successful_task(self):
        from rainforge_worker.request_routing import fast_route
        self.worker.runtime.generate.side_effect = lambda prompt, emit, cancelled, *args, **kwargs: emit('Уточните задачу.')
        with patch.object(self.engine, 'model_plan') as planner:
            self.run_turn('Привет')
            status, _ = self.run_turn('делай')
        self.assertNotIn('effective_goal', status)
        planner.assert_not_called()

    def test_spoken_reply_stops_repeating_and_saves_only_one_copy(self):
        def generate(prompt, emit, cancelled, tokens, **options):
            self.assertEqual(tokens, 112)
            self.assertEqual(options["repeat_penalty"], 1.15)
            for _ in range(30):
                emit("Расскажу подсказку. ")
            self.fail("The repeating stream was never stopped")

        self.worker.runtime.generate.side_effect = generate
        with patch.object(self.engine, "model_plan") as planner:
            status, assistant = self.run_turn("Как дела?", mode="voice")
        self.assertEqual(status["state"], "answered")
        self.assertEqual(status["message"], "Расскажу подсказку.")
        self.assertEqual(assistant["text"], status["message"])
        planner.assert_not_called()

    def test_explicit_voice_launch_uses_one_tool_without_planner(self):
        with patch.object(self.engine, "model_plan") as planner:
            status, _ = self.run_turn("Open https://example.org/", mode="voice")
        self.assertEqual(status["route"], "task")
        self.assertEqual((status["planner_calls"], status["tool_calls"]), (0, 1))
        self.worker.browser.navigate.assert_called_once_with("https://example.org/")
        planner.assert_not_called()
        with self.assertRaises(ValueError):
            self.worker.dispatch("task.start", {"goal": "Read the screen", "mode": "voice", "allow_interaction": True})

    def test_voice_python_misrecognition_still_selects_python_tool(self):
        from rainforge_worker.actions import coding_request, single_python_request
        transcript = 'Создай склит на петоне, напиши функцию, которая складывает 2 числа.'
        self.assertTrue(coding_request(transcript))
        self.assertTrue(single_python_request(transcript))
        self.assertFalse(coding_request('Создай рисунок петонеи'))

    def test_colloquial_russian_request_routes_to_coding(self):
        from rainforge_worker.actions import coding_request
        from rainforge_worker.request_routing import fast_route, identity_reply
        goal = "Короче, смотри, тебе нужно сейчас мне написать игру на Пайтоне и сохранить её на рабочем столе."
        self.assertIn(fast_route(goal), {"task", "agent"})
        self.assertTrue(coding_request(goal))
        self.assertIsNotNone(identity_reply("Нексус, слушай, кто ты?"))

    def test_legacy_identity_bypasses_planner(self):
        with patch.object(self.engine, "model_plan") as planner:
            started = self.worker.dispatch("task.start", {"goal": "Кто ты?", "budget": "day"})
            self.engine.thread.join(timeout=5)
            status = self.worker.dispatch("task.status", {"run_id": started["id"]})
            sync = self.worker.dispatch("task.execute", {"goal": "Кто ты?"})
        self.assertEqual(status["state"], "answered")
        self.assertEqual(status["route"], "chat")
        self.assertEqual(status["planner_calls"], 0)
        self.assertEqual(status["tool_calls"], 0)
        self.assertEqual(sync["message"], status["message"])
        self.worker.runtime.generate.assert_not_called()
        planner.assert_not_called()

    def test_direct_voice_turn_cannot_open_apps_or_web(self):
        with patch.object(self.engine, "model_plan") as planner:
            started = self.worker.dispatch("task.start", {"goal": "Open https://example.org/", "mode": "chat"})
            self.engine.thread.join(timeout=5)
            status = self.worker.dispatch("task.status", {"run_id": started["id"]})
        self.assertEqual(status["state"], "answered")
        self.assertEqual(status["route"], "chat")
        self.assertEqual(status["tool_calls"], 0)
        self.assertTrue(status["voice_task_hint"])
        self.worker.browser.navigate.assert_not_called()
        planner.assert_not_called()
        with self.assertRaises(ValueError):
            self.worker.dispatch("task.start", {"goal": "Read the screen", "mode": "chat", "allow_interaction": True})

    def test_identity_in_auto_mode_never_loads_model_or_plans(self):
        self.worker.runtime.status.return_value = {"state": "stopped"}
        expected = (
            "Я Nexus — ИИ-ассистент. Могу отвечать на вопросы, помогать с "
            "программированием и выполнять задачи с помощью доступных инструментов"
        )
        with patch.object(self.engine, "model_plan") as planner, patch.object(
            self.worker.workspace_tools, "launch"
        ) as launch, patch.object(self.worker.desktop, "inspect") as inspect:
            status, assistant = self.run_turn("Кто ты?", budget="day")
        self.assertEqual(status["route"], "chat")
        self.assertEqual(status["state"], "answered")
        self.assertEqual(status["message"], expected)
        self.assertEqual(assistant["text"], expected)
        self.assertEqual(status["results"], [])
        self.assertEqual((status["planner_calls"], status["tool_calls"]), (0, 0))
        self.assertFalse(status.get("plan"))
        planner.assert_not_called()
        launch.assert_not_called()
        inspect.assert_not_called()
        self.worker.runtime.start.assert_not_called()
        self.worker.runtime.generate.assert_not_called()
        self.assertEqual(self.worker.browser.mock_calls, [])

    def test_chat_followup_keeps_history_without_tools(self):
        prompts = []

        def generate(prompt, emit, cancelled, *args, **kwargs):
            self.assertIsNone(kwargs.get("schema"))
            prompts.append((prompt, kwargs.get("history", [])))
            emit("Ответ из диалога.")

        self.worker.runtime.generate.side_effect = generate
        with patch.object(self.engine, "model_plan") as planner:
            first, _ = self.run_turn("Что такое переменная в Python?")
            second, assistant = self.run_turn("Как это связано с областью видимости?")
        self.assertEqual([first["route"], second["route"]], ["chat", "chat"])
        self.assertEqual(second["state"], "answered")
        self.assertEqual(assistant["text"], "Ответ из диалога.")
        self.assertEqual(len(prompts), 2)
        self.assertEqual(prompts[1], ("Как это связано с областью видимости?", [
            {"role": "user", "content": "Что такое переменная в Python?"},
            {"role": "assistant", "content": "Ответ из диалога."},
        ]))
        planner.assert_not_called()
        self.assertEqual(self.worker.browser.mock_calls, [])

    def test_code_snippet_request_uses_chat_without_file_writes(self):
        self.worker.runtime.generate.side_effect = (
            lambda prompt, emit, cancelled, *args, **kwargs: emit("def add(a, b): return a + b")
        )
        with patch.object(self.engine, "model_plan") as planner, patch.object(
            self.worker.workspace_tools, "write"
        ) as write:
            status, _ = self.run_turn("Напиши функцию на Python, которая складывает два числа.")
        self.assertEqual(status["route"], "chat")
        self.assertIn("def add", status["message"])
        self.assertEqual((status["planner_calls"], status["tool_calls"]), (0, 0))
        planner.assert_not_called()
        write.assert_not_called()

    def test_ambiguous_request_uses_classifier_before_planning(self):
        def generate(prompt, emit, cancelled, *args, **kwargs):
            if kwargs.get("schema"):
                emit('{"route":"task"}')
            else:
                emit("Fixture evidence")

        self.worker.runtime.generate.side_effect = generate
        plans = [
            {"actions": [{"tool": "open_url", "url": "https://example.org/"}]},
            {"actions": [{"tool": "finish", "summary": "Observed page"}]},
        ]
        goal = "It would help if the browser displayed https://example.org/."
        with patch.object(self.engine, "model_plan", side_effect=plans) as planner:
            status, _ = self.run_turn(goal)
        self.assertEqual(status["route"], "task")
        self.assertGreaterEqual(planner.call_count, 1)
        self.worker.browser.navigate.assert_called_once_with("https://example.org/")
        first = self.worker.runtime.generate.call_args_list[0]
        self.assertIn(goal, first.args[0])
        self.assertEqual(first.kwargs["schema"]["properties"]["route"]["enum"],
                         ["chat", "task", "agent"])
        self.assertEqual(sum(bool(call.kwargs.get("schema"))
                             for call in self.worker.runtime.generate.call_args_list), 1)

    def test_invalid_classifier_output_falls_back_to_chat(self):
        outputs = iter(["incomplete classifier response", "I can help with that."])
        self.worker.runtime.generate.side_effect = (
            lambda prompt, emit, cancelled, *args, **kwargs: emit(next(outputs))
        )
        with patch.object(self.engine, "model_plan") as planner:
            status, _ = self.run_turn("That sounds useful.")
        self.assertEqual(status["route"], "chat")
        self.assertEqual(status["state"], "answered")
        self.assertEqual(status["message"], "I can help with that.")
        self.assertEqual(self.worker.runtime.generate.call_count, 2)
        planner.assert_not_called()
        self.assertEqual(self.worker.browser.mock_calls, [])

    def test_direct_navigation_needs_one_tool_and_no_planner(self):
        with patch.object(self.engine, "model_plan") as planner:
            status, _ = self.run_turn("Open https://example.org/", budget="day")
        self.assertEqual(status["route"], "task")
        self.assertEqual(status["state"], "observed")
        self.assertEqual(len(status["results"]), 1)
        self.assertIn("Observed page", status["message"])
        self.worker.browser.navigate.assert_called_once_with("https://example.org/")
        planner.assert_not_called()
        self.worker.runtime.generate.assert_not_called()

    def test_focused_task_does_not_inherit_day_long_agent_ceiling(self):
        def choose(*args, **kwargs):
            return {"actions": [{
                "tool": "search_web", "query": "Python release " + str(planner.call_count),
            }]}

        self.worker.runtime.generate.side_effect = (
            lambda prompt, emit, cancelled, *args, **kwargs: emit("Fixture evidence")
        )
        with patch.object(self.engine, "model_plan", side_effect=choose) as planner:
            status, _ = self.run_turn("Find the latest Python release on the web.", budget="day")
        self.assertEqual(status["route"], "task")
        self.assertEqual(status["state"], "partial")
        self.assertEqual(status["max_steps"], 6)
        self.assertLessEqual(status["max_seconds"], 600)
        self.assertEqual(planner.call_count, 6)
        self.assertEqual(self.worker.browser.navigate.call_count, 6)
        self.assertEqual(len(status["results"]), 6)

    def test_multi_step_agent_uses_observations_and_stops_at_result(self):
        goal = "Research RainForge on the web, then compare the results and verify the source."
        plans = [
            {"actions": [{"tool": "search_web", "query": "RainForge"}]},
            {"actions": [{"tool": "open_url", "url": "https://example.org/"}]},
            {"actions": [{"tool": "finish", "summary": "Fixture evidence"}]},
        ]
        self.worker.runtime.generate.side_effect = (
            lambda prompt, emit, cancelled, *args, **kwargs: emit("Fixture evidence")
        )
        with patch.object(self.engine, "model_plan", side_effect=plans) as planner:
            status, assistant = self.run_turn(goal, budget="day")
        self.assertEqual(status["route"], "agent")
        self.assertEqual(status["max_steps"], 256)
        self.assertEqual(planner.call_count, 3)
        self.assertIn("Fixture evidence", str(planner.call_args.kwargs["history"]))
        self.assertEqual(self.worker.browser.navigate.call_count, 2)
        self.assertTrue(assistant["text"].startswith("Fixture evidence"))

    def test_compound_navigation_uses_first_url_without_consuming_later_steps(self):
        goal = "Open https://example.org/ and read the current page."
        self.worker.browser.inspect.return_value = {
            "url": "https://example.org/",
            "title": "Read after navigation",
            "text": "Fixture evidence",
        }
        plans = [
            {"actions": [{"tool": "open_url", "url": "https://example.org/"}]},
            {"actions": [{"tool": "browser_read"}]},
            {"actions": [{"tool": "finish", "summary": "Fixture evidence"}]},
        ]
        self.worker.runtime.generate.side_effect = (
            lambda prompt, emit, cancelled, *args, **kwargs: emit("Fixture evidence")
        )
        with patch.object(self.engine, "model_plan", side_effect=plans) as planner:
            status, _ = self.run_turn(goal)
        self.assertEqual(status["route"], "agent")
        self.assertEqual((status["planner_calls"], status["tool_calls"]), (3, 2))
        self.assertEqual(planner.call_args_list[0].kwargs["hint"], {"actions": [{"tool": "open_url", "url": "https://example.org/"}]})
        self.worker.browser.navigate.assert_called_once_with("https://example.org/")
        self.worker.browser.inspect.assert_called_once()
        self.assertIn("Read after navigation", status["message"])

    def test_agent_stops_repeat_before_second_tool_call(self):
        goal = "Research RainForge on the web, then compare the results and verify the source."
        repeated = {"actions": [{"tool": "search_web", "query": "RainForge"}]}
        self.worker.runtime.generate.side_effect = (
            lambda prompt, emit, cancelled, *args, **kwargs: emit("Fixture evidence")
        )
        with patch.object(self.engine, "model_plan", return_value=repeated) as planner:
            status, _ = self.run_turn(goal)
        self.assertEqual(status["route"], "agent")
        self.assertEqual(status["state"], "partial")
        self.assertEqual(len(status["results"]), 1)
        self.assertEqual(planner.call_count, 2)
        self.worker.browser.navigate.assert_called_once()

    def test_multi_file_programming_uses_workspace_and_bounded_plan(self):
        goal = "Create a multi-file Python project and add tests and documentation."
        outputs = iter([
            json.dumps({"files": [
                {"path": "calc.py", "purpose": "Arithmetic function"},
                {"path": "test_calc.py", "purpose": "Unit test"},
                {"path": "README.md", "purpose": "Usage"},
            ]}),
            "def add(a, b):\n    return a + b\n",
            "import unittest\nfrom calc import add\n\n"
            "class TestAdd(unittest.TestCase):\n    def test_add(self):\n"
            "        self.assertEqual(add(1, 2), 3)\n",
            "# Calculator\n\nRun tests with python -m unittest.\n",
        ])
        self.worker.runtime.generate.side_effect = (
            lambda prompt, emit, cancelled, *args, **kwargs: emit(next(outputs))
        )
        with patch.object(self.engine, "model_plan", return_value={
            "actions": [{"tool": "create_project", "path": "calc"}],
        }) as planner:
            status, _ = self.run_turn(goal)
        self.assertEqual(status["route"], "agent")
        self.assertEqual(status["state"], "project_created")
        planner.assert_called_once()
        self.assertEqual(self.worker.runtime.generate.call_count, 4)
        result = status["results"][0]
        self.assertFalse(result["behavior_verified"])
        self.assertEqual({entry["path"].split("/")[-1] for entry in result["files"]},
                         {"calc.py", "test_calc.py", "README.md"})
        self.assertIn("return a + b", self.worker.workspace_tools.read(
            result["directory"] + "/calc.py"
        )["text"])
        self.assertEqual(self.worker.browser.mock_calls, [])

    def test_agent_route_does_not_authorize_interaction(self):
        goal = "Click Continue on the current page, then verify that it contains DONE."
        with patch.object(self.engine, "model_plan", return_value={
            "actions": [{"tool": "browser_click", "selector": "#continue"}],
        }):
            status, _ = self.run_turn(goal)
        self.assertEqual(status["route"], "agent")
        self.assertEqual(status["state"], "failed")
        self.assertIn("Разрешите", status["error"])
        self.worker.browser.act.assert_not_called()
        self.worker.browser.act_observed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
