# Iterative browser agent — scoped development feature

Open **Браузер**, navigate to a prepared site, then use **Браузерный агент**. Enter the goal, an expected-result CSS selector (default `body`) and the text that must appear there. Choose 3–8 steps. Click/type operations require the displayed permission checkbox; without it only observation and waiting are allowed. Start the agent and review its human-readable progress or expand technical details. Stop cancels future steps; actions already performed are not rolled back.

The tested workflow uses a controlled HTML form: enter a name, press Continue, find a newly created Confirm button with a dynamic ID, then wait for a delayed greeting in `#result`. The application, actual local Qwen Coder 1.5B and actual Chrome/Edge DOM perform the steps. GUI and frozen-worker acceptance use CDP only to provision the local fixture before the run; workflow decisions/actions are not mocked. These are controlled test workloads, not evidence that arbitrary public websites or all apps work.

## Execution

The controller observes → checks the expected condition → asks the model for one action → validates → records intent → executes → observes again. The model can type into an observed input, click an observed button/link, observe, wait or stop. It cannot invent JavaScript, CSS action selectors, shell commands or element refs. The caller's CSS selector is used only for the outcome check. The general main-composer `task.execute` planner remains separate and static; iterative browser mode is currently in Browser Studio.

Each fresh observation enumerates up to 80 visible supported controls and binds short refs to actual DOM nodes in a [CDP isolated execution world](https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-createIsolatedWorld). Page scripts cannot replace that world's observation map. A node must still be connected, visible, enabled, on the observed URL and have the same fingerprint before action. Replaced/changed nodes return `not_applied`; the next iteration obtains new refs. A snapshot expires after 45 seconds. Password/file/hidden inputs are excluded. Typing an already matching value skips dispatching input/change events.

The planner receives a bounded, goal-ranked subset of up to 20 controls, limited page text and three recent events. Full goal/expected condition are preserved; observations are pruned to a conservative context budget. This byte estimate is not an exact tokenizer. Large goals can require a larger context. If no model is loaded, the installed text model preference is a small coder, loaded in Auto with a 4096-token context. A manually loaded model/context is retained.

Agent instructions use a separate system message. Page content and history are untrusted data. The native engine uses [llama.cpp schema-constrained response format](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md#post-v1chatcompletions-openai-compatible-chat-completions-api) with current capability/ref enums; an independent validator checks the result again. Internal system/schema options are not accepted by public `chat.begin` RPC. This reduces malformed decisions but does not make model decisions inherently trustworthy.

After a click has no observable change, that action is removed from the model's current options until state changes; it is also rejected before dispatch if proposed again. This does not prove the original click had no hidden server effect, which is why it is not blindly repeated. The same masking prevents repeated no-op text writes. Two invalid decisions stop the run. Up to eight decision steps and a 180-second controller budget are enforced at checkpoints; an in-flight browser command/model startup can take additional time before cancellation/deadline checks. A planner generation is capped at 30 seconds. Stopping generation unloads its owned local model, as in the existing cancellation path.

## Proof, ownership and recovery

The model cannot declare completion. `checks_passed` requires the browser engine to observe the caller's exact substring in the selected visible element. It proves that condition at that time, not the complete meaning of an arbitrary goal. A weak condition (for example, text already present before execution) can pass immediately. Step exhaustion, stop-before-result, invalid plans or errors do not produce a verified proof.

One shared action lock reserves the managed browser against manual browser RPCs and the static action planner while the iterative run is active. Manual controls are disabled in the GUI; backend calls are rejected as reserved. This is not full model-resource scheduling: other model controls can still interfere and cause an explicit failed run.

Runs are atomically journaled under `.data/browser-agent`. Intent is saved before dispatch. Transport errors with unknown action outcome stop the run and preserve the pending intent. Restart marks unfinished runs `interrupted`; it does not replay them. Inspect the actual page before retrying. An explicit new run is a new authorization, not automatic recovery. The browser profile/history are retained; cleanup UI remains pending.

The runner stays on the initial HTTP/HTTPS origin. Obvious external link/form targets and new-window targets are not dispatched, and an observed origin change stops further work. Arbitrary page JavaScript can itself make network requests or redirects; this is not a browser network sandbox. Initial navigation still uses the existing public-URL policy; authorized localhost/private-network previews need a separate capability and are pending. Loopback fixtures are permitted only by the source test harness or provisioned locally through the owned browser test setup. Cross-origin workflows, frames, shadow DOM, file upload/download, account authorization, publishing, purchases, messages and destructive workflows are outside the tested feature scope. The existing permission model is not complete natural-language authorization or complete prompt-injection protection.

## Evidence and reproduction

Before the schema/masking fixes, the real 1.5B model clicked Continue on an empty field and later emitted multiple JSON objects or repeated the ineffective click. The corrected controlled workflow passed in source, GUI (with another name), and frozen-worker tests. Extra ineffective initial decisions remain visible; no claim of perfect planning or broad reliability is made.

```powershell
$env:RF_BROWSER_TEST='1'
.venv\Scripts\python.exe -m unittest discover -s tests -p test_browser_agent.py -v
.venv\Scripts\python.exe scripts/test_browser_agent_model.py --models-dir "C:\Users\Ilya\Desktop\RainForge NEXUS\runtime\models"
.venv\Scripts\python.exe scripts/test_browser_agent_packaged.py --models-dir "C:\Users\Ilya\Desktop\RainForge NEXUS\runtime\models"
```

For the full real-model GUI check set `RF_BROWSER_AGENT_MODEL_TEST=1`, `RF_TEST_MODELS_DIR` to the installed weights folder, plus `RF_BROWSER_TEST=1` and `RF_HF_DATASET_TEST=1`, then run the documented npm E2E command. Internet is used only for initial public navigation/other live connector tests; the controlled agent fixture is provisioned locally. See `docs/evidence/browser-agent-*.json` and its actual UI screenshot.
