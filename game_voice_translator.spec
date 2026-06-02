# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
root = Path.cwd()

datas = [
    (str(root / "config.json"), "."),
    (str(root / ".models"), ".models"),
    (str(root / ".venv-win" / "Lib" / "site-packages" / "faster_whisper" / "assets"), "faster_whisper/assets"),
]

site_packages = root / ".venv-win" / "Lib" / "site-packages"
system32 = Path("C:/Windows/System32")
binaries = [
    (str(site_packages / "ctranslate2" / "cudnn64_9.dll"), "ctranslate2"),
    (str(system32 / "msvcp140.dll"), "PyQt5/Qt5/bin"),
    (str(system32 / "msvcp140_1.dll"), "PyQt5/Qt5/bin"),
    (str(system32 / "msvcp140_2.dll"), "PyQt5/Qt5/bin"),
]

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
    "qrcode",
    "PIL.Image",
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
    "librosa",
    "numba",
    "llvmlite",
    "scipy",
    "tensorflow",
    "tensorboard",
    "sklearn",
    "pandas",
]

a = Analysis(
    ["main.py"],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GameVoiceTranslator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="GameVoiceTranslator",
)
