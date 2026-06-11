from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from loguru import logger

from voxgo.app_info import APP_VERSION, GITHUB_REPOSITORY, USER_AGENT
from voxgo.runtime.dll_paths import configure_local_dll_paths
from voxgo.update.checker import DEFAULT_UPDATE_MANIFEST_URL, fetch_update_manifest


CUDA_RUNTIME_ARCHIVE_NAME = f"VoxGo-v{APP_VERSION}-cuda-runtime.zip"
CUDA_RUNTIME_DOWNLOAD_URL = (
    f"https://github.com/{GITHUB_REPOSITORY}/releases/download/"
    f"v{APP_VERSION}/{CUDA_RUNTIME_ARCHIVE_NAME}"
)
CUDA_RUNTIME_REQUIRED_DLLS = (
    "cublas64_12.dll",
    "cublasLt64_12.dll",
    "cudart64_12.dll",
    "cudnn64_9.dll",
)


@dataclass(frozen=True)
class GpuDetectionResult:
    has_nvidia: bool
    adapter_names: tuple[str, ...] = field(default_factory=tuple)
    error: str = ""


@dataclass(frozen=True)
class CudaRuntimeInstallResult:
    runtime_dir: Path
    dlls: tuple[str, ...]
    downloaded_bytes: int = 0
    url: str = CUDA_RUNTIME_DOWNLOAD_URL


def writable_runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def local_cuda_runtime_dir(runtime_root: Optional[Path] = None) -> Path:
    root = Path(runtime_root) if runtime_root else writable_runtime_root()
    return root / "runtime" / "cuda"


def cuda_runtime_candidate_dirs() -> tuple[Path, ...]:
    dirs = [local_cuda_runtime_dir()]
    bundle_root = getattr(sys, "_MEIPASS", "")
    if getattr(sys, "frozen", False) and bundle_root:
        dirs.append(Path(bundle_root).resolve() / "runtime" / "cuda")
    unique = []
    seen = set()
    for directory in dirs:
        key = str(directory).lower()
        if key not in seen:
            seen.add(key)
            unique.append(directory)
    return tuple(unique)


def missing_local_cuda_runtime_dlls(
    runtime_dir: Optional[Path] = None,
    required_dlls: Iterable[str] = CUDA_RUNTIME_REQUIRED_DLLS,
) -> tuple[str, ...]:
    required = tuple(required_dlls)
    if runtime_dir:
        directory = Path(runtime_dir)
        return tuple(name for name in required if not (directory / name).is_file())

    best_missing = required
    for directory in cuda_runtime_candidate_dirs():
        missing = tuple(name for name in required if not (directory / name).is_file())
        if not missing:
            return ()
        if len(missing) < len(best_missing):
            best_missing = missing
    return tuple(best_missing)


def has_local_cuda_runtime(runtime_dir: Optional[Path] = None) -> bool:
    return not missing_local_cuda_runtime_dlls(runtime_dir)


def resolve_cuda_runtime_download_url(
    manifest_url: str = DEFAULT_UPDATE_MANIFEST_URL,
    fetcher: Optional[Callable[[str], dict]] = None,
) -> str:
    try:
        fetcher = fetcher or (lambda url: fetch_update_manifest(url, user_agent=USER_AGENT))
        manifest = fetcher(manifest_url)
        latest = str(manifest.get("latest", "")).strip().lstrip("v")
        url = str(manifest.get("cuda_runtime_url") or "").strip()
        if latest == APP_VERSION and url:
            return url
    except Exception as exc:
        logger.debug("Unable to resolve CUDA runtime URL from update manifest: {}", exc)
    return CUDA_RUNTIME_DOWNLOAD_URL


def detect_nvidia_gpu(timeout_seconds: int = 6) -> GpuDetectionResult:
    names: list[str] = []
    errors: list[str] = []
    probes = (
        (
            "powershell",
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }",
            ],
        ),
        (
            "wmic",
            ["wmic", "path", "win32_VideoController", "get", "name"],
        ),
    )
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    for label, command in probes:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                creationflags=creationflags,
                check=False,
            )
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            continue
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            errors.append(f"{label}: exit {completed.returncode} {detail}".strip())
            continue
        parsed = _parse_gpu_names(completed.stdout)
        if parsed:
            names.extend(parsed)
            break
    unique_names = tuple(dict.fromkeys(names))
    return GpuDetectionResult(
        has_nvidia=any("nvidia" in name.lower() for name in unique_names),
        adapter_names=unique_names,
        error="; ".join(error for error in errors if error),
    )


def download_and_install_cuda_runtime(
    progress_callback: Optional[Callable[[int, int], None]] = None,
    url: str = CUDA_RUNTIME_DOWNLOAD_URL,
    runtime_dir: Optional[Path] = None,
) -> CudaRuntimeInstallResult:
    destination = Path(runtime_dir) if runtime_dir else local_cuda_runtime_dir()
    if has_local_cuda_runtime(destination):
        return CudaRuntimeInstallResult(
            runtime_dir=destination,
            dlls=_installed_dll_names(destination),
            downloaded_bytes=0,
            url=url,
        )

    destination.mkdir(parents=True, exist_ok=True)
    archive_path = destination.parent / CUDA_RUNTIME_ARCHIVE_NAME
    part_path = archive_path.with_suffix(archive_path.suffix + ".part")
    if part_path.exists():
        part_path.unlink()

    logger.info("Downloading CUDA runtime from {}", url)
    downloaded = 0
    total = 0
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            total = int(response.headers.get("Content-Length") or 0)
            with part_path.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total)
        part_path.replace(archive_path)
        result = install_cuda_runtime_archive(archive_path, destination)
        return CudaRuntimeInstallResult(
            runtime_dir=result.runtime_dir,
            dlls=result.dlls,
            downloaded_bytes=downloaded,
            url=url,
        )
    finally:
        part_path.unlink(missing_ok=True)
        archive_path.unlink(missing_ok=True)


def install_cuda_runtime_archive(
    archive_path: Path,
    runtime_dir: Optional[Path] = None,
) -> CudaRuntimeInstallResult:
    destination = Path(runtime_dir) if runtime_dir else local_cuda_runtime_dir()
    destination.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="voxgo-cuda-runtime-", dir=str(destination.parent)))
    try:
        dll_paths = _extract_cuda_runtime_dlls(Path(archive_path), staging)
        if not dll_paths:
            raise ValueError("CUDA runtime archive does not contain DLL files.")
        for dll_path in dll_paths:
            shutil.copy2(dll_path, destination / dll_path.name)
        missing = missing_local_cuda_runtime_dlls(destination)
        if missing:
            raise ValueError(f"CUDA runtime archive is missing required DLLs: {', '.join(missing)}")
        configure_local_dll_paths()
        return CudaRuntimeInstallResult(
            runtime_dir=destination,
            dlls=_installed_dll_names(destination),
            downloaded_bytes=0,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _parse_gpu_names(output: str) -> list[str]:
    names = []
    for line in (output or "").splitlines():
        name = line.strip()
        if not name or name.lower() == "name":
            continue
        names.append(name)
    return names


def _extract_cuda_runtime_dlls(archive_path: Path, staging: Path) -> list[Path]:
    extracted = []
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            safe_name = _safe_archive_member_basename(member.filename)
            if not safe_name.lower().endswith(".dll"):
                continue
            target = staging / safe_name
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            extracted.append(target)
    return extracted


def _safe_archive_member_basename(member_name: str) -> str:
    normalized = (member_name or "").replace("\\", "/")
    path = Path(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe path in CUDA runtime archive: {member_name}")
    name = path.name
    if not name:
        raise ValueError(f"Invalid path in CUDA runtime archive: {member_name}")
    return name


def _installed_dll_names(runtime_dir: Path) -> tuple[str, ...]:
    return tuple(sorted(path.name for path in Path(runtime_dir).glob("*.dll")))
