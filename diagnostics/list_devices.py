try:
    import pyaudiowpatch as pyaudio
    HAS_WASAPI_LOOPBACK = True
except ImportError:
    import pyaudio
    HAS_WASAPI_LOOPBACK = False

p = pyaudio.PyAudio()
print("=== 所有音频设备 ===")
if HAS_WASAPI_LOOPBACK and hasattr(p, "get_loopback_device_info_generator"):
    print("\n=== 系统声音设备（优先选择这些）===")
    for info in p.get_loopback_device_info_generator():
        print(
            f"  [{info['index']}] LOOPBACK {info.get('name', '')} "
            f"{int(info.get('defaultSampleRate', 0))}Hz/"
            f"{int(info.get('maxInputChannels', 0))}ch"
        )
else:
    print("\n未安装 PyAudioWPatch，只能看到麦克风/立体声混音等普通输入设备。")

print("\n=== 全部设备 ===")
for i in range(p.get_device_count()):
    info = p.get_device_info_by_index(i)
    name = info.get("name", "")
    is_input = info.get("maxInputChannels", 0) > 0
    is_output = info.get("maxOutputChannels", 0) > 0
    is_loopback = bool(info.get("isLoopbackDevice")) or "loopback" in name.lower()
    marker = "* LOOPBACK" if is_loopback else ""
    direction = "IN" if is_input else "OUT"
    if is_input and is_output:
        direction = "IO"
    print(f"  [{i}] {direction} {marker} {name}")
    if is_loopback:
        print(f"      采样率: {info['defaultSampleRate']}Hz")
p.terminate()
