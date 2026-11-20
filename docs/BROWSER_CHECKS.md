# Browser waits and result checks

In **Браузер**, open a page, enter a unique CSS selector, then use **Дождаться элемента**. Enter expected visible text and use **Проверить текст** to wait for that substring in the selected element. Both GUI operations allow five seconds. A missing/hidden/ambiguous element or missing text fails explicitly. Password values are excluded. For normal inputs the check reads their value; for other elements it reads visible inner text.

The underlying `browser.wait` RPC accepts `selector`, optional `text`, and a timeout of 0.1–10 seconds. It checks at roughly 100 ms intervals with bounded CDP command deadlines. A success returns the exact condition, a bounded actual-text sample, check time, elapsed time and page observation. It proves that condition at observation time, not the entire user's goal, future state or arbitrary external effects. The interface clears its previous proof when the selector/expectation changes or a subsequent action starts.

The typed planner can use `browser_wait` and `browser_assert`. Explicit assertions produce `checks_passed` only after the browser engine actually verifies a condition. Model text alone cannot create that proof. The current planner still returns a static list of at most three actions; general observation/replanning, iframe/shadow DOM, semantic selector recovery and visual fallback remain pending.

Requests recognized by the narrow `check that` / `verify that` / `проверь, что` / `убедись, что` prefix rule are constrained to browser read/wait/assert tools before execution. Mutating model plans are rejected. This is a scoped deterministic restriction, not complete natural-language authorization or prompt-injection protection. Other operations retain their existing permission limitations.

The managed browser uses a fresh separate profile. On Windows its process is assigned to an owned kill-on-close Job. Normal close waits for process exit; controller termination was tested to terminate the owned browser root process. Profiles are retained under `.data` and retention UI is still pending. The feature does not claim a hostile-code VM sandbox or control over the user's ordinary browser profile.

Actual checks:

- A local fixture updates its result 350 ms after a click; the wait observes the expected new result. Negative text, hidden/ambiguous elements and password-field checks fail.
- Real browser UI navigation/check succeeds, then a deliberately absent text fails and the prior success indicator disappears.
- Abrupt controller termination exits the owned browser root process.
- Real packaged Qwen2.5-Coder-1.5B generated `browser_assert(body, "Learn more")` and the browser verified it. Before the read-only prompt/restriction fix, this model incorrectly clicked a matching link. The corrected single case passed in 2.735 seconds; it is not a broad reliability benchmark.

Run source checks with `RF_BROWSER_TEST=1`; E2E uses the prepared venv when available. The live public-page E2E and `scripts/test_model_browser_check.py --models-dir <installed-model-directory>` require internet and the relevant browser/model. Remote page contents can change. See `docs/evidence/model-browser-check.json` and `docs/evidence/desktop-browser-check.json` for the tested scope.


Update: Browser Studio now also has a separate iterative agent with fresh DOM refs and condition proof; see BROWSER_AGENT.md. The main-composer static planner described above remains separate.
