# Windows native build
Build frontend/worker first, then python scripts/build_windows.py. It verifies pinned WebView2 SDK, invokes Windows Framework64 csc, and emits outputs/windows/RainForgeSetup.exe. The tested host uses Windows x64, .NET Framework 4.8, WebView2 and compatible Visual C++ Runtime libraries. Python, Node and Ollama are unnecessary on the user machine. Automatic setup of the Windows prerequisites remains pending.

Normal installation uses LocalAppData/Programs/RainForgeNexus; user data/models use LocalAppData/RainForgeNexus/data. Updates retain previous app-directory backups. Removal preserves data and unknown files. The standard bundle includes the basic SmolLM2 chat model and Whisper speech recognition model. Nexus loads the chat model on first use and recognizes speech locally. The installer is unsigned. Clean-machine testing remains pending.

Existing desktop shortcut uses RainForge.exe with explicit original .data and runtime/models arguments. Earlier browser app windows use the old launcher.

Run python scripts/test_windows_release.py and python scripts/test_native_recovery.py for real native lifecycle tests. Source lives under apps/windows-native. References: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution and https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.4314.35 . SDK license included in payload.
