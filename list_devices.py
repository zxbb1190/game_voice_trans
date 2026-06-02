import pyaudio
p = pyaudio.PyAudio()
print("=== 所有音频设备 ===")
for i in range(p.get_device_count()):
    info = p.get_device_info_by_index(i)
    name = info.get("name", "")
    is_input = info.get("maxInputChannels", 0) > 0
    is_output = info.get("maxOutputChannels", 0) > 0
    is_loopback = "loopback" in name.lower()
    marker = "⭐ LOOPBACK" if is_loopback else ""
    direction = "IN" if is_input else "OUT"
    if is_input and is_output:
        direction = "IO"
    print(f"  [{i}] {direction} {marker} {name}")
    if is_loopback:
        print(f"      采样率: {info['defaultSampleRate']}Hz")
p.terminate()