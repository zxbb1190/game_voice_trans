"""
Update manifest fetching and version comparison for VoxGo.
"""

import json
import re
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, List, Optional
from urllib.parse import urlparse


DEFAULT_UPDATE_MANIFEST_URL = "https://voxgo.cn/update.json"
UPDATE_CHECK_INTERVAL_SECONDS = 24 * 60 * 60
UPDATE_CHANNELS = ("stable", "beta")


@dataclass
class UpdateSettings:
    enabled: bool = True
    channel: str = "stable"
    last_check_at: float = 0
    ignored_version: str = ""
    manifest_url: str = DEFAULT_UPDATE_MANIFEST_URL


@dataclass
class UpdateInfo:
    latest: str
    channel: str = "stable"
    title: str = ""
    notes: List[str] = field(default_factory=list)
    release_url: str = ""
    download_lite_url: str = ""
    download_full_url: str = ""
    sha256_lite: str = ""
    sha256_full: str = ""
    mandatory: bool = False
    raw: dict = field(default_factory=dict)

    def display_title(self) -> str:
        return self.title or f"VoxGo v{self.latest}"


@dataclass
class UpdateCheckResult:
    status: str
    update: Optional[UpdateInfo] = None
    message: str = ""
    checked_at: float = field(default_factory=time.time)


def normalize_update_channel(value: str) -> str:
    channel = (value or "").strip().lower()
    return channel if channel in UPDATE_CHANNELS else "stable"


def should_check_for_update(settings: UpdateSettings, now: Optional[float] = None) -> bool:
    if not getattr(settings, "enabled", True):
        return False
    now = time.time() if now is None else now
    try:
        last_check_at = float(getattr(settings, "last_check_at", 0) or 0)
    except Exception:
        last_check_at = 0
    return now - last_check_at >= UPDATE_CHECK_INTERVAL_SECONDS


def compare_versions(left: str, right: str) -> int:
    left_key = _version_key(left)
    right_key = _version_key(right)
    if left_key == right_key:
        return 0
    return 1 if left_key > right_key else -1


def is_newer_version(latest: str, current: str) -> bool:
    return compare_versions(latest, current) > 0


def fetch_update_manifest(url: str, user_agent: str = "VoxGo", timeout: int = 8) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": user_agent,
        },
    )
    opener = _direct_opener_for_local_url(url)
    open_url = opener.open if opener else urllib.request.urlopen
    with open_url(request, timeout=timeout) as response:
        body = response.read(1024 * 1024)
    return json.loads(body.decode("utf-8-sig"))


def parse_update_manifest(data: dict) -> UpdateInfo:
    if not isinstance(data, dict):
        raise ValueError("update manifest must be a JSON object")
    latest = str(data.get("latest", "")).strip().lstrip("v")
    if not latest:
        raise ValueError("update manifest missing latest")
    notes = data.get("notes", [])
    if isinstance(notes, str):
        notes = [line.strip("- ").strip() for line in notes.splitlines() if line.strip()]
    elif not isinstance(notes, list):
        notes = []
    notes = [str(note).strip() for note in notes if str(note).strip()]
    channel = normalize_update_channel(str(data.get("channel", "stable")))
    return UpdateInfo(
        latest=latest,
        channel=channel,
        title=str(data.get("title") or f"VoxGo v{latest}").strip(),
        notes=notes,
        release_url=str(data.get("release_url") or "").strip(),
        download_lite_url=str(data.get("download_lite_url") or "").strip(),
        download_full_url=str(data.get("download_full_url") or "").strip(),
        sha256_lite=str(data.get("sha256_lite") or "").strip(),
        sha256_full=str(data.get("sha256_full") or "").strip(),
        mandatory=bool(data.get("mandatory", False)),
        raw=dict(data),
    )


def check_for_update(
    current_version: str,
    settings: UpdateSettings,
    fetcher: Optional[Callable[[str], dict]] = None,
    manual: bool = False,
    user_agent: str = "VoxGo",
) -> UpdateCheckResult:
    if not manual and not getattr(settings, "enabled", True):
        return UpdateCheckResult("disabled", message="已关闭自动检查更新")

    url = getattr(settings, "manifest_url", "") or DEFAULT_UPDATE_MANIFEST_URL
    fetcher = fetcher or (lambda manifest_url: fetch_update_manifest(manifest_url, user_agent=user_agent))
    try:
        manifest = fetcher(url)
        update = parse_update_manifest(manifest)
    except Exception as exc:
        return UpdateCheckResult("error", message=str(exc))

    desired_channel = normalize_update_channel(getattr(settings, "channel", "stable"))
    if not _channel_matches(update.channel, desired_channel):
        return UpdateCheckResult(
            "channel_mismatch",
            update=update,
            message=f"当前通道为 {desired_channel}，远端通道为 {update.channel}",
        )
    if not is_newer_version(update.latest, current_version):
        return UpdateCheckResult("current", update=update, message="当前已是最新版本")

    ignored_version = str(getattr(settings, "ignored_version", "") or "").strip().lstrip("v")
    if ignored_version and compare_versions(update.latest, ignored_version) == 0 and not update.mandatory:
        return UpdateCheckResult("ignored", update=update, message=f"已忽略 v{update.latest}")

    return UpdateCheckResult("available", update=update, message=f"发现新版本 {update.display_title()}")


def _channel_matches(manifest_channel: str, desired_channel: str) -> bool:
    manifest_channel = normalize_update_channel(manifest_channel)
    desired_channel = normalize_update_channel(desired_channel)
    if desired_channel == "beta":
        return manifest_channel in ("stable", "beta")
    return manifest_channel == desired_channel


def _direct_opener_for_local_url(url: str):
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        host = ""
    if host in ("127.0.0.1", "localhost", "::1"):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return None


def _version_key(version: str):
    text = str(version or "0").strip().lower()
    text = text[1:] if text.startswith("v") else text
    main, prerelease = _split_prerelease(text)
    numbers = [int(part) for part in re.findall(r"\d+", main)]
    while len(numbers) < 4:
        numbers.append(0)
    prerelease_rank = 1 if not prerelease else 0
    prerelease_numbers = [int(part) for part in re.findall(r"\d+", prerelease)]
    while len(prerelease_numbers) < 2:
        prerelease_numbers.append(0)
    return tuple(numbers[:4] + [prerelease_rank] + prerelease_numbers[:2])


def _split_prerelease(text: str):
    for sep in ("-", "+"):
        if sep in text:
            main, suffix = text.split(sep, 1)
            return main, suffix
    return text, ""
