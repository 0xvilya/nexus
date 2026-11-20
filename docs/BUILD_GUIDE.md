# Build and verification guide

## Development prerequisites

Recommended builder: Windows x64, Python 3.12+, Node.js supported by pinned Vite, npm, Rust stable MSVC target, Visual Studio C++ Build Tools and Windows SDK, WebView2. This session actually used Python 3.11.9, Node 24.22.1 and installed WebView2. Rust/MSVC/SDK were absent.

These are developer prerequisites. The intended release bundles the worker interpreter and llama.cpp and must not require users to install Python, Node or Ollama. That release criterion has not yet been proven.

## Source checks and development UI

```powershell
python scripts/run_checks.py
python scripts/prepare_runtime.py --accept-license --with-smoke-model
python scripts/run_checks.py --native
cd apps/desktop
npm ci
npm run build
npm run test:e2e
cd ../..
powershell -File scripts/dev_ui.ps1
```

Playwright currently uses installed Microsoft Edge headlessly. It launches a worker at port 8845, stores temporary data/results under `work/`, and stops its server on exit. `dev_ui.ps1` serves port 8844 until Ctrl+C. It does not add startup entries or scheduled tasks.

This Codex environment supplies node.exe without npm on PATH. The session prepared npm 12.2.1 under the chat's `work/tools/npm/package/bin/npm-cli.js`; invoke it with node in place of npm. It is a development convenience outside the product, not an end-user requirement.

## Packaged worker

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-build.txt
.venv/Scripts/python.exe scripts/build_worker.py
```

Output: `apps/desktop/src-tauri/binaries/rainforge-worker-x86_64-pc-windows-msvc.exe`. Keep console stdio enabled in this sidecar; the Tauri parent suppresses a visible console with CREATE_NO_WINDOW. Run it with `--data-dir <absolute path> --runtime-dir <absolute path>` and JSON-RPC lines on stdin. Optional `--models-dir` points at writable app-local storage.

## Tauri / installer — blocked on this host

After the native developer toolchain exists, build the sidecar, prepare the runtime, then:

```powershell
cd apps/desktop
npm run tauri -- dev
npm run tauri -- build
```

The configured installer target is per-user NSIS. Verify the sidecar path, bundled resources, writable model storage, worker restart, close/crash cleanup, WebView2 handling and an actual clean-machine install. Rust crates currently specify Tauri major version 2 without a generated Cargo.lock because cargo is unavailable; pin the resolved lock after the first verified build.

Standard distribution should include runtime + notices and let the GUI download a model. An Offline Bundle additionally needs the model, its license, attribution and redistribution review. No installer or signing certificate is currently produced.

## References checked during implementation

- [Tauri Windows prerequisites](https://v2.tauri.app/start/prerequisites/)
- [Tauri external binaries](https://v2.tauri.app/develop/sidecar/)
- [llama.cpp server API](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
- [Pinned runtime release](https://github.com/ggml-org/llama.cpp/releases/tag/b12032)
- [Quantized smoke model](https://huggingface.co/bartowski/SmolLM2-135M-Instruct-GGUF)
- [Base model and license](https://huggingface.co/HuggingFaceTB/SmolLM2-135M-Instruct)

Browser tool source runtime now needs websocket-client==1.9.0 (included in requirements-build.txt). Run browser tests with the project venv and RF_BROWSER_TEST=1; clean stdlib-only test runs skip the opt-in browser test.


Current native build: see WINDOWS_INSTALLER.md (WinForms/WebView2 compiled with existing Framework csc). After frontend/worker builds run `python scripts/build_windows.py`, then `python scripts/test_windows_release.py`. Dataset checks: see DATASET_STUDIO.md. Live HF tests require RF_HF_DATASET_TEST=1.


Browser-agent acceptance: BROWSER_AGENT.md documents source/model/frozen and GUI commands. E2E accepts RF_TEST_MODELS_DIR so existing installed weights can be used without copying them. RF_BROWSER_AGENT_MODEL_TEST=1 enables the real 1.5B GUI case; RF_BROWSER_TEST=1 and RF_HF_DATASET_TEST=1 enable other live checks. Normal tests retain explicit SKIPPED reasons when not enabled.
