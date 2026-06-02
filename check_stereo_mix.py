import pyaudio
p = pyaudio.PyAudio()
idx = 18  # 立体声混音
info = p.get_device_info_by_index(idx)
print(f"设备[{idx}]: {info['name']}")
print(f"  默认采样率: {info['defaultSampleRate']}")
print(f"  最大输入通道: {info['maxInputChannels']}")
print(f"  最大输出通道: {info['maxOutputChannels']}")
# 测试支持的采样率
for sr in [8000, 16000, 22050, 44100, 48000, 96000]:
    try:
        supported = p.is_format_supported(sr, input_device=idx, 
            input_channels=1, input_format=pyaudio.paInt16)
        print(f"  {sr}Hz: {'支持' if supported else '不支持'}")
    except:
        print(f"  {sr}Hz: 不支持")
p.terminate()