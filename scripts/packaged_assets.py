"""Include the pinned offline voice engine and starter model in the Windows bundle."""
import json
import importlib.metadata
import subprocess
import sys
import shutil
import tarfile
import zipfile
from pathlib import Path

from rainforge_native.download import download, sha256

WHISPER = (
    "https://github.com/ggml-org/whisper.cpp/releases/download/v1.8.3/whisper-bin-x64.zip",
    "d824b1e37599f882b396e73f1ee0bfd5d0529f700314c48311dcbd00b803321d",
    3968674,
)
VOICE_MODEL = (
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-base-q5_1.bin",
    "422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898",
    59707625,
)
SHERPA_TTS = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.8/sherpa-onnx-v1.13.8-win-x64-shared-MD-MinSizeRel-lib.tar.bz2",
    "3d3afa1da26239d51e9efcd4831537897d8a6a50cefa980461bfa93e24b2b0a3",
    6175461,
)
DMITRI = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-ru_RU-dmitri-medium-int8.tar.bz2",
    "7636793307f634ce54c6e65528a91a61683114f1a6635a08caf64ba6c54e6a63",
    21129441,
)
LICENSES = {
    "LICENSE-onnxruntime.txt": (
        "https://raw.githubusercontent.com/microsoft/onnxruntime/v1.28.2/LICENSE",
        "2f07c72751aed99790b8a4869cf2311df85a860b22ded05fa22803587a48922c",
        1073,
    ),
    "NOTICE-onnxruntime.txt": (
        "https://raw.githubusercontent.com/microsoft/onnxruntime/v1.28.2/ThirdPartyNotices.txt",
        "0e07b95f3a8d6230037707c5c4a2b554d12c4cb67369669ac255635528ffcee2",
        325054,
    ),
    "LICENSE-espeak-ng.txt": (
        "https://raw.githubusercontent.com/csukuangfj/espeak-ng/ed530aa113046142eb5115cf2fc9157854d0ffe1/COPYING",
        "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903",
        35147,
    ),
    "LICENSE-piper-phonemize.txt": (
        "https://raw.githubusercontent.com/csukuangfj/piper-phonemize/f3ff95afc03640bc1399e113e83361192a2fafb4/LICENSE.md",
        "13746d509d74e55ea2265fbef204bb7cdbf84a8315b0207e988326cb54387028",
        1071,
    ),
    "LICENSE-whisper.cpp.txt": (
        "https://raw.githubusercontent.com/ggml-org/whisper.cpp/v1.8.3/LICENSE",
        "e562a2ddfaf8280537795ac5ecd34e3012b6582a147ef69ba6a6a5c08c84757d",
        1078,
    ),
    "LICENSE-Whisper-model.txt": (
        "https://raw.githubusercontent.com/openai/whisper/v20250625/LICENSE",
        "b5d65a59060e68c4ff940e1eddfa6f94b2d68fdf58ed7f4dd57721c997e35e9d",
        1063,
    ),
    "LICENSE-SmolLM2.txt": (
        "https://www.apache.org/licenses/LICENSE-2.0.txt",
        "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
        11358,
    ),
}


def include_speech(cache: Path, bundle: Path):
    speech = bundle / "runtime" / "speech"
    speech.mkdir(parents=True, exist_ok=True)
    for asset in (SHERPA_TTS, DMITRI):
        url, digest, size = asset
        archive_name = url.rsplit("/", 1)[-1]
        archive = download(url, cache / archive_name, digest, size)
        with tarfile.open(archive, "r:bz2") as package:
            for member in package:
                path = Path(member.name)
                if not member.isfile() or len(path.parts) < 2 or path.drive or path.is_absolute() or ".." in path.parts:
                    continue
                relative = Path(*path.parts[1:])
                if asset == SHERPA_TTS:
                    if relative.as_posix() not in {
                        "lib/onnxruntime.dll", "lib/onnxruntime_providers_shared.dll", "lib/sherpa-onnx-c-api.dll"
                    }:
                        continue
                    relative = Path(relative.name)
                elif relative.parts[0] != "espeak-ng-data" and relative.as_posix() not in {
                    "MODEL_CARD", "ru_RU-dmitri-medium.onnx", "tokens.txt"
                }:
                    continue
                destination = speech / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                with package.extractfile(member) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)


def include_offline_assets(root: Path, bundle: Path, include_neural_speech=True):
    cache = root / "work" / "voice-runtime"
    if include_neural_speech:
        include_speech(cache, bundle)
    elif (bundle / "runtime" / "speech").exists():
        raise ValueError("Core assets require a staging directory without neural speech files")
    url, digest, size = WHISPER
    archive = download(url, cache / "whisper-v1.8.3.zip", digest, size)
    voice = bundle / "runtime" / "voice"
    voice.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        for name in ("whisper-cli.exe", "whisper.dll", "ggml.dll", "ggml-base.dll", "ggml-cpu.dll"):
            (voice / name).write_bytes(package.read("Release/" + name))
    url, digest, size = VOICE_MODEL
    shutil.copy2(download(url, cache / "ggml-base-q5_1.bin", digest, size), voice / "ggml-base-q5_1.bin")

    catalog = json.loads((root / "runtime/catalog.json").read_text(encoding="utf-8"))
    model = next(item for item in catalog["models"] if item["id"] == "smollm2-135m-smoke")
    filename = model["filename"]
    local = root / "runtime" / "models" / filename
    if not local.is_file() or sha256(local) != model["sha256"]:
        local = download(model["url"], cache / filename, model["sha256"], model["bytes"])
    starter = bundle / "runtime" / "starter"
    starter.mkdir(parents=True, exist_ok=True)
    shutil.copy2(local, starter / filename)
    speech_notices = {"LICENSE-onnxruntime.txt", "NOTICE-onnxruntime.txt",
                      "LICENSE-espeak-ng.txt", "LICENSE-piper-phonemize.txt"}
    for name, (url, digest, size) in LICENSES.items():
        if not include_neural_speech and name in speech_notices:
            if (bundle / name).exists():
                raise ValueError("Core staging contains an optional speech notice")
            continue
        shutil.copy2(download(url, cache / name, digest, size), bundle / name)
    notice = (root / "docs/OFFLINE_ASSETS_NOTICE.txt").read_text(encoding="utf-8")
    if not include_neural_speech:
        notice = notice.split("Russian synthesis:", 1)[0]
        notice += ("Windows Core preview\n\nOptional neural speech synthesis is not bundled. "
                   "Voice playback uses a language-compatible installed Windows SAPI voice when available.\n")
    (bundle / "OFFLINE_ASSETS_NOTICE.txt").write_text(notice, encoding="utf-8")


def include_worker_notices(root: Path, bundle: Path):
    """Copy interpreter and bootloader notices from the worker build environment."""
    try:
        distribution = importlib.metadata.distribution("pyinstaller")
        copies = [distribution.locate_file(item) for item in distribution.files
                  if item.name == "COPYING.txt"]
        if len(copies) != 1:
            raise ValueError("PyInstaller has no unique COPYING.txt")
        source = {"python_license": str(Path(sys.base_prefix) / "LICENSE.txt"),
                  "pyinstaller_license": str(copies[0]),
                  "python_version": sys.version.split()[0],
                  "pyinstaller_version": distribution.version}
    except importlib.metadata.PackageNotFoundError:
        builder = root / ".venv" / "Scripts" / "python.exe"
        if not builder.is_file():
            raise ValueError("Build with the same prepared Python environment as build_worker.py") from None
        query = ("import importlib.metadata,json,sys;from pathlib import Path;"
                 "d=importlib.metadata.distribution('pyinstaller');"
                 "files=[d.locate_file(f) for f in d.files if f.name=='COPYING.txt'];"
                 "assert len(files)==1;"
                 "print(json.dumps({'python_license':str(Path(sys.base_prefix)/'LICENSE.txt'),"
                 "'pyinstaller_license':str(files[0]),'python_version':sys.version.split()[0],"
                 "'pyinstaller_version':d.version}))")
        source = json.loads(subprocess.check_output([str(builder), "-c", query], text=True, timeout=15))
    for name, key in (("LICENSE-Python.txt", "python_license"), ("LICENSE-PyInstaller.txt", "pyinstaller_license")):
        path = Path(source[key])
        if not path.is_file() or path.stat().st_size > 500000:
            raise ValueError("The worker build environment is missing " + name)
        shutil.copy2(path, bundle / name)
    (bundle / "WORKER_NOTICES.json").write_text(json.dumps({
        "python_version": source["python_version"], "pyinstaller_version": source["pyinstaller_version"],
        "licenses": ["LICENSE-Python.txt", "LICENSE-PyInstaller.txt"],
    }, indent=2) + "\n", encoding="utf-8")
