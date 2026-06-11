"""Backward-compatible VoxGo entry point."""

from voxgo.runtime.dll_paths import configure_local_dll_paths

configure_local_dll_paths()

from voxgo.app import VoxGoApp, main

__all__ = ["VoxGoApp", "main"]


if __name__ == "__main__":
    main()
