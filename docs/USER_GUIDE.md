# Using the current development build

Open **RainForge NEXUS** on the Windows desktop. The shortcut launches the native application using `C:\Users\Ilya\Desktop\RainForge NEXUS`. Its `.data` and `runtime/models` folders hold existing user data and models. Closing the native window stops its owned worker/model processes. No background startup service is installed.

The folder also contains RainForgeSetup.exe. See WINDOWS_INSTALLER.md before installing a separate per-user copy; its default data location differs from this portable Desktop deployment. Python, Node.js and Ollama are not needed to run the bundle. WebView2 Runtime and .NET Framework 4.6.2+ currently must already be installed.

## Tasks

The default **Действие** mode routes site-opening requests directly and other supported requests through a bounded model plan. Opened pages are read through an owned Chrome/Edge profile. This is not a general unlimited agent loop. Unsupported tasks must report an error instead of creating a note and pretending success.

**Создать заметку** explicitly creates a Markdown artifact with read-back SHA-256 verification. **Локальный чат** generates text; text alone does not execute code or prove a task was completed. Notes and task history persist in SQLite. Project chat history is still pending.

## Sections

- **Модели**: search/filter the twelve curated entries, review licenses, download with resume/cancel, import a compatible local GGUF, select CPU/GPU/hybrid/Auto and load it. GPU requires the included compatible Vulkan runtime/driver; hybrid is layer offload, not arbitrary live VRAM paging. Manual screenshot handoff uses a separately installed supported visual model and text model sequentially.
- **Код**: bounded text files in the dedicated coding workspace, SHA conflict checks, Python/JSON syntax, model code drafts. Tests require explicit code-execution consent. AutoFix currently edits one Python implementation file once and reruns unittest; execution is not an OS sandbox.
- **Браузер**: owned Chrome/Edge navigation, page observation, CSS input/click. Auth may require user interaction; password filling/CAPTCHA bypass is not provided.
- **Рабочий стол**: choose an actual window, inspect accessible controls, use available Invoke/Value patterns. Support depends on the target application's accessibility provider. Not all applications expose usable controls.
- **Агенты**: custom role profiles and sequential text task chains, with handoff, persistence, cancellation and reports. These teams currently generate unverified text, rather than running arbitrary tools independently.
- **Подключения**: public searches and supported account connection probes. DPAPI protects entered tokens. Full private mail/document/message actions and OAuth refresh remain pending.
- **Датасеты**: public Hugging Face file search/download or local CSV/JSONL file selection; then clean, deduplicate, split and verify locally. No model required. Review the declared license first. See DATASET_STUDIO.md for limits, outputs and recovery.
- **Артефакты**: verified Markdown notes. Dataset outputs currently appear in Dataset Studio with their own paths and hashes.

Ctrl+K opens section/task search. Toolbar controls change RU/EN labels, theme and inspector visibility. Some newer studios still have Russian-only labels. Use a sufficiently wide window for all panels. Errors and unsupported capabilities remain visible; this build is not the finished original specification.

Browser checks: enter a selector and optional expected text, then use Дождаться элемента / Проверить текст. A successful check proves only that displayed condition at observation time. See BROWSER_CHECKS.md. Dataset file replacement clears the prior source license/provenance; use Использовать скачанный файл explicitly to reuse a previous verified HF download.


## Браузерный агент
Во вкладке Браузер откройте подготовленный сайт. В новом блоке Браузерный агент задайте цель и текст, который должен появиться в результате; по умолчанию проверяется тело страницы. Разрешите нажатия/ввод только для нужного задания и запустите. Агент выбирает по одному действию после свежего наблюдения. Видны шаги, ошибки, остановка и подтверждённое условие. После сбоя действия сами не повторяются. Это отдельный режим: основной чат пока использует прежний ограниченный план. Подробности и границы — BROWSER_AGENT.md.
