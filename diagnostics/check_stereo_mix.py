"""
Check whether a selected audio input device supports common capture rates.
"""

import argparse

try:
    import pyaudiowpatch as pyaudio

    HAS_WASAPI_LOOPBACK = True
except ImportError:
    import pyaudio

    HAS_WASAPI_LOOPBACK = False


COMMON_SAMPLE_RATES = (8000, 16000, 22050, 44100, 48000, 96000)


def _device_label(info) -> str:
    direction = "IN" if info.get("maxInputChannels", 0) else "OUT"
    if info.get("maxInputChannels", 0) and info.get("maxOutputChannels", 0):
        direction = "IO"
    loopback = " LOOPBACK" if info.get("isLoopbackDevice") else ""
    return f"[{info['index']}] {direction}{loopback} {info.get('name', '')}"


def list_candidates(audio):
    print("=== 可测试音频设备 ===")
    for i in range(audio.get_device_count()):
        info = audio.get_device_info_by_index(i)
        name = str(info.get("name", "") or "")
        is_input = info.get("maxInputChannels", 0) > 0
        is_loopback = bool(info.get("isLoopbackDevice")) or "loopback" in name.lower()
        if is_input or is_loopback or "立体声混音" in name or "stereo mix" in name.lower():
            print(f"  {_device_label(info)}")
    if not HAS_WASAPI_LOOPBACK:
        print("\n未安装 PyAudioWPatch，只能看到麦克风/立体声混音等普通输入设备。")


def check_device(audio, index: int):
    info = audio.get_device_info_by_index(index)
    print(f"设备: {_device_label(info)}")
    print(f"  默认采样率: {info['defaultSampleRate']}")
    print(f"  最大输入通道: {info['maxInputChannels']}")
    print(f"  最大输出通道: {info['maxOutputChannels']}")
    input_channels = max(1, int(info.get("maxInputChannels", 0) or 1))
    for sample_rate in COMMON_SAMPLE_RATES:
        try:
            supported = audio.is_format_supported(
                sample_rate,
                input_device=index,
                input_channels=input_channels,
                input_format=pyaudio.paInt16,
            )
            status = "支持" if supported else "不支持"
        except Exception:
            status = "不支持"
        print(f"  {sample_rate}Hz: {status}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("index", nargs="?", type=int, help="device index to test")
    args = parser.parse_args()

    audio = pyaudio.PyAudio()
    try:
        if args.index is None:
            list_candidates(audio)
            print("\n用法: python diagnostics/check_stereo_mix.py <device_index>")
            return
        check_device(audio, args.index)
    finally:
        audio.terminate()


if __name__ == "__main__":
    main()
