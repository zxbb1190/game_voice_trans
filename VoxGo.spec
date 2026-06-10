# -*- mode: python ; coding: utf-8 -*-

import json
import os
import shutil
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
root = Path.cwd()
include_model = os.environ.get("INCLUDE_MODEL", "0") == "1"
include_cuda_runtime = os.environ.get("INCLUDE_CUDA_RUNTIME", "0") == "1"
safe_config = root / "build" / "packaged_config" / "config.json"
safe_config.parent.mkdir(parents=True, exist_ok=True)
safe_config.write_text(
    (root / "config.example.json").read_text(encoding="utf-8"),
    encoding="utf-8",
)

datas = [
    (str(safe_config), "."),
    (str(root / "assets" / "voxgo.ico"), "assets"),
    (str(root / "voxgo" / "mobile" / "static"), "voxgo/mobile/static"),
    (str(root / ".venv-win" / "Lib" / "site-packages" / "faster_whisper" / "assets"), "faster_whisper/assets"),
]

if include_model:
    safe_models = root / "build" / "packaged_models" / ".models"
    shutil.rmtree(safe_models.parent, ignore_errors=True)
    safe_models.mkdir(parents=True, exist_ok=True)
    cachedir_tag = root / ".models" / "CACHEDIR.TAG"
    if cachedir_tag.exists():
        shutil.copy2(cachedir_tag, safe_models / "CACHEDIR.TAG")

    config_data = json.loads((root / "config.example.json").read_text(encoding="utf-8"))
    whisper_config = config_data.get("whisper", {})
    model_sizes = {
        whisper_config.get("model_size", "small"),
        whisper_config.get("fast_model_size", ""),
    }
    if whisper_config.get("pure_english_environment", False):
        model_sizes.add(whisper_config.get("english_model_size", "small.en"))
        model_sizes.add(whisper_config.get("fast_english_model_size", ""))
    for model_size in sorted(str(item or "").strip() for item in model_sizes):
        if not model_size:
            continue
        model_repo = f"models--Systran--faster-whisper-{model_size}"
        source_repo = root / ".models" / model_repo
        if not source_repo.exists():
            raise FileNotFoundError(f"Packaged Whisper model cache is missing: {source_repo}")

        snapshot = (source_repo / "refs" / "main").read_text(encoding="utf-8").strip()
        safe_repo = safe_models / model_repo
        (safe_repo / "refs").mkdir(parents=True, exist_ok=True)
        (safe_repo / "snapshots" / snapshot).mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_repo / "refs" / "main", safe_repo / "refs" / "main")
        for source in (source_repo / "snapshots" / snapshot).iterdir():
            if source.is_file() or source.is_symlink():
                shutil.copy2(
                    source,
                    safe_repo / "snapshots" / snapshot / source.name,
                    follow_symlinks=True,
                )
    datas.append((str(safe_models), ".models"))

site_packages = root / ".venv-win" / "Lib" / "site-packages"
system32 = Path("C:/Windows/System32")
binaries = [
    (str(system32 / "msvcp140.dll"), "PyQt5/Qt5/bin"),
    (str(system32 / "msvcp140_1.dll"), "PyQt5/Qt5/bin"),
    (str(system32 / "msvcp140_2.dll"), "PyQt5/Qt5/bin"),
]
if include_cuda_runtime:
    binaries.append((str(site_packages / "ctranslate2" / "cudnn64_9.dll"), "ctranslate2"))

hiddenimports = [
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "websockets.legacy",
    "websockets.legacy.server",
    "huggingface_hub",
    "huggingface_hub.file_download",
    "tqdm",
    "tqdm.auto",
    "qrcode",
    "PIL.Image",
    "soxr",
]
hiddenimports += collect_submodules("setuptools._vendor.backports")

excludes = [
    "pytest",
    "matplotlib",
    "IPython",
    "notebook",
    "torch",
    "torchaudio",
    "torchvision",
    "tensorflow",
    "tensorboard",
    "sklearn",
    "pandas",
    "librosa",
    "scipy",
    "numba",
    "llvmlite",
    "soundfile",
    "requests",
    "sounddevice",
    "pynput",
    "pyqt5_tools",
    "httptools",
    "watchfiles",
]

a = Analysis(
    ["voxgo/app.py"],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(root / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

if not include_cuda_runtime:
    cuda_runtime_names = {
        "cudnn64_9.dll",
        "cublas64_12.dll",
        "cublaslt64_12.dll",
        "cudart64_12.dll",
    }
    a.binaries = [
        item
        for item in a.binaries
        if Path(item[0]).name.lower() not in cuda_runtime_names
        and Path(item[1]).name.lower() not in cuda_runtime_names
    ]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VoxGo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(root / "assets" / "voxgo.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="VoxGo",
)
