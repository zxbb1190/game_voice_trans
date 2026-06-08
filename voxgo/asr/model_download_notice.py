from voxgo.asr.whisper_engine import ModelDownloadProgress, describe_model_download_source


def format_download_amount(downloaded: int, total: int, percent: float) -> str:
    if total:
        return f"{format_bytes(downloaded)} / {format_bytes(total)} ({percent:.1f}%)"
    if downloaded:
        return format_bytes(downloaded)
    return ""


def format_bytes(value: int) -> str:
    value = max(0, int(value or 0))
    units = ("B", "KB", "MB", "GB")
    amount = float(value)
    unit = units[0]
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            break
        amount /= 1024
    if unit == "B":
        return f"{int(amount)} {unit}"
    return f"{amount:.1f} {unit}"


class ModelDownloadNoticeFormatter:
    def __init__(self, config_getter):
        self._config_getter = config_getter
        self.last_download_bytes = (0, 0, 0.0)

    def record_progress(self, progress: ModelDownloadProgress):
        if progress.downloaded_bytes or progress.total_bytes:
            self.last_download_bytes = (
                progress.downloaded_bytes,
                progress.total_bytes,
                progress.percent,
            )

    def format(self, progress: ModelDownloadProgress) -> str:
        config = self._config_getter()
        model = progress.model_name or config.whisper.model_size
        repo = progress.repo_id or model
        source = progress.source or describe_model_download_source(
            getattr(config.whisper, "model_download_source", "modelscope"),
            getattr(config.whisper, "model_download_endpoint", ""),
        )
        header = f"模型: {model}\n仓库: {repo}\n来源: {source}"

        if progress.status == "checking":
            return f"{header}\n正在检查本地缓存"
        if progress.status == "complete":
            detail = progress.message or "模型缓存已就绪"
            downloaded, total, percent = self.last_download_bytes
            progress_line = format_download_amount(downloaded, total, percent)
            if progress_line:
                return f"{header}\n已下载: {progress_line}\n{detail}，正在加载识别引擎"
            return f"{header}\n{detail}，正在加载识别引擎"
        if progress.status == "error":
            downloaded, total, percent = self.last_download_bytes
            progress_line = format_download_amount(downloaded, total, percent)
            suffix = f"\n已下载: {progress_line}" if progress_line else ""
            return f"{progress.message or '模型下载失败'}{suffix}"

        progress_line = format_download_amount(
            progress.downloaded_bytes,
            progress.total_bytes,
            progress.percent,
        )
        if not progress_line:
            progress_line = "正在准备下载"
        return f"{header}\n已下载: {progress_line}"
