"""Backward-compatible VoxGo entry point."""

from voxgo.app import VoxGoApp, main

__all__ = ["VoxGoApp", "main"]


if __name__ == "__main__":
    main()
