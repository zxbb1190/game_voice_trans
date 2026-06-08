from loguru import logger

from voxgo.audio.capture import (
    SAFE_MAX_SPEECH_THRESHOLD_DBFS,
    SystemAudioCapture,
    list_input_devices,
)


class AudioRuntime:
    def __init__(self, config_getter, speech_callback, notify_user, write_crash_report):
        self._config_getter = config_getter
        self._speech_callback = speech_callback
        self._notify_user = notify_user
        self._write_crash_report = write_crash_report
        self.capture = None

    def start(self, notice_title: str = "音频捕获已启动", reuse_noise_gate: bool = False):
        config = self._config_getter()
        previous_noise_gate = None
        if self.capture:
            if reuse_noise_gate and hasattr(self.capture, "current_noise_gate"):
                previous_noise_gate = self.capture.current_noise_gate()
            self.capture.stop()
            self.capture = None
        if previous_noise_gate and previous_noise_gate[2]:
            config.audio.initial_noise_floor_dbfs = previous_noise_gate[0]
            config.audio.initial_energy_threshold_dbfs = min(
                float(previous_noise_gate[1]),
                SAFE_MAX_SPEECH_THRESHOLD_DBFS,
            )
        else:
            config.audio.initial_noise_floor_dbfs = None
            config.audio.initial_energy_threshold_dbfs = None
        self.capture = SystemAudioCapture(config.audio)
        self.capture.set_speech_callback(self._speech_callback)
        self.capture.start()
        self._notify_user(
            notice_title,
            f"{self.describe_selected_device()} -> mono",
            "状态",
        )

    def restart(self, on_error, reuse_noise_gate: bool = False):
        try:
            self.start("音频设置已更新", reuse_noise_gate=reuse_noise_gate)
        except Exception as exc:
            on_error(exc)

    def stop(self):
        if self.capture:
            self.capture.stop()

    def process_tick(self, running: bool, paused: bool):
        if not running or paused:
            return
        if self.capture:
            self.capture.process_audio()

    def describe_selected_device(self) -> str:
        config = self._config_getter()
        if not self.capture or not self.capture.selected_device:
            if config.audio.input_device_index is not None:
                return f"[{config.audio.input_device_index}] {config.audio.input_device_name}"
            return "自动选择"
        device = self.capture.selected_device
        return (
            f"{device['type']} [{device['index']}]: {device['name']} "
            f"({device['sample_rate']}Hz/{device['channels']}ch)"
        )

    def list_devices(self):
        try:
            return list_input_devices()
        except Exception as exc:
            self._write_crash_report("音频设备枚举失败", exc)
            logger.warning("音频设备枚举失败: {}", exc)
            self._notify_user("音频设备枚举失败", str(exc), "错误")
            return []
