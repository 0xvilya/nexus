# Nexus Windows Core preview

This build contains the Windows x64 native Nexus application, its bundled Python worker, CPU llama.cpp inference, a small SmolLM2 chat model and local Whisper dictation.

Core excludes optional neural speech synthesis, Dmitri, sherpa-onnx, ONNX Runtime, the Piper phonemizer, eSpeak NG data and the Vulkan runtime. Voice playback falls back to a compatible installed Windows SAPI voice. Availability and language depend on the Windows installation. Core does not download another speech engine automatically.

The default full development build remains separate and can retain its existing offline speech assets. Core does not replace or remove a user's installed full build while being packaged.

Build from a prepared Windows checkout after frontend and worker builds.

    .venv/Scripts/python.exe scripts/build_windows.py --core
    .venv/Scripts/python.exe scripts/test_windows_release.py --core

The isolated staging folder is work/windows-core-release/bundle. The installer is outputs/windows-core/RainForgeSetup.exe. The matching build.json records the variant and SHA-256. Every payload contains BUILD_VARIANT.json and this guide as WINDOWS_CORE.txt.

The preview is unsigned. The current Windows host requires .NET Framework 4.8, WebView2 Runtime and compatible Visual C++ Runtime libraries. Host installation checks do not establish clean-machine compatibility. The installer preserves user data when updating or uninstalling through its supported paths.

There is no native macOS Nexus application in this release.

Upstream component versions, hashes and their included notices remain pinned by runtime/catalog.json and scripts/packaged_assets.py. The core speech notice lists only the bundled dictation and starter-model assets.


Worker redistribution notices include LICENSE-Python.txt, LICENSE-PyInstaller.txt and WORKER_NOTICES.json from the matching prepared builder environment. The PyInstaller file includes its bootloader exception.
