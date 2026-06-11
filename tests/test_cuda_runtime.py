import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from voxgo.runtime.cuda_runtime import (  # noqa: E402
    CUDA_RUNTIME_DOWNLOAD_URL,
    CUDA_RUNTIME_REQUIRED_DLLS,
    has_local_cuda_runtime,
    install_cuda_runtime_archive,
    missing_local_cuda_runtime_dlls,
    resolve_cuda_runtime_download_url,
)


class CudaRuntimeTest(unittest.TestCase):
    def test_missing_local_cuda_runtime_dlls(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)

            self.assertFalse(has_local_cuda_runtime(runtime_dir))

            for name in CUDA_RUNTIME_REQUIRED_DLLS:
                (runtime_dir / name).write_bytes(b"dll")

            self.assertEqual(missing_local_cuda_runtime_dlls(runtime_dir), ())
            self.assertTrue(has_local_cuda_runtime(runtime_dir))

    def test_install_cuda_runtime_archive_copies_dlls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_path = root / "cuda.zip"
            destination = root / "runtime" / "cuda"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for name in CUDA_RUNTIME_REQUIRED_DLLS:
                    archive.writestr(f"runtime/cuda/{name}", b"dll")
                archive.writestr("notes.txt", "ignored")

            result = install_cuda_runtime_archive(archive_path, destination)

            self.assertEqual(set(CUDA_RUNTIME_REQUIRED_DLLS), set(result.dlls))
            for name in CUDA_RUNTIME_REQUIRED_DLLS:
                self.assertTrue((destination / name).is_file())

    def test_install_cuda_runtime_archive_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_path = root / "cuda.zip"
            destination = root / "runtime" / "cuda"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../evil.dll", b"dll")

            with self.assertRaises(ValueError):
                install_cuda_runtime_archive(archive_path, destination)

    def test_resolve_cuda_runtime_download_url_falls_back_without_manifest(self):
        self.assertEqual(
            resolve_cuda_runtime_download_url(fetcher=lambda url: {}),
            CUDA_RUNTIME_DOWNLOAD_URL,
        )

    def test_resolve_cuda_runtime_download_url_uses_current_manifest(self):
        url = "https://example.com/VoxGo-cuda-runtime.zip"

        self.assertEqual(
            resolve_cuda_runtime_download_url(
                fetcher=lambda _: {"latest": "0.3.1", "cuda_runtime_url": url}
            ),
            url,
        )


if __name__ == "__main__":
    unittest.main()
