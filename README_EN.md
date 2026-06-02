# Game Voice Translator

Chinese documentation: [README.md](README.md)

An open-source real-time voice translation overlay for PC gamers, designed for overseas servers, guild voice chat, Discord teammates, and live-stream subtitle assistance.

## Features
- **System-audio capture**: Captures Windows playback audio through WASAPI Loopback, not the microphone.
- **Local speech recognition**: Uses faster-whisper for offline speech-to-text and can auto-detect Chinese or English.
- **Bidirectional translation**: Automatically translates English to Chinese and Chinese to English.
- **OpenAI-compatible APIs**: Works with Chat Completions-compatible providers such as SiliconFlow, DeepSeek, Qwen, GLM, OpenAI-compatible gateways, and local models.
- **In-game overlay**: Transparent always-on-top PyQt overlay for translation results.
- **Visible status and error messages**: Startup status, selected audio device, pause/resume events, API status codes, and provider error messages are shown in the overlay.
- **Mobile mirror**: Pushes translations to a browser on the same LAN through WebSocket.
- **Global hotkeys**: Toggle overlay, clear history, and pause/resume translation.

## Project Layout
```text
game_voice_translator/
├── main.py               # Main application
├── audio_capture.py      # Windows system-audio capture
├── speech_recognition.py # faster-whisper speech recognition
├── translator.py         # OpenAI-compatible translation client
├── overlay.py            # PyQt5 overlay window
├── mobile_server.py      # Mobile WebSocket server
├── config.example.json   # Configuration template
├── config.json           # Local config, ignored by Git
├── requirements.txt      # Python dependencies
├── install.bat           # Windows installer script
├── run.bat               # Windows launcher
├── README.md             # Chinese documentation
└── README_EN.md          # English documentation
```

## Quick Start

### 1. Install Dependencies
Double-click `install.bat`, or run:
```bash
pip install -r requirements.txt
```

### 2. Configure Translation API
Copy the example config first:
```bat
copy config.example.json config.json
```

Then edit `config.json`, or start the app and use the gear button in the overlay to configure API Key, model name, and compatible endpoint:
```json
"translation": {
  "api_key": "YOUR_OPENAI_COMPATIBLE_API_KEY",
  "model": "Qwen/Qwen2.5-7B-Instruct",
  "endpoint": "https://api.siliconflow.cn/v1/chat/completions"
}
```

The default template uses SiliconFlow with `Qwen/Qwen2.5-7B-Instruct`. SiliconFlow provides free models/quota; register and create an API Key to use it. Availability and quota are subject to the provider's current policy.

Register/get API Key: <https://cloud.siliconflow.cn/i/iA6DF2nP>

Common OpenAI-compatible examples:

| Provider | Compatible endpoint example | Model example |
|----------|-----------------------------|---------------|
| SiliconFlow | `https://api.siliconflow.cn/v1/chat/completions` | `Qwen/Qwen2.5-7B-Instruct` |
| DeepSeek | `https://api.deepseek.com/chat/completions` | `deepseek-v4-flash` |
| Qwen / Alibaba Model Studio | `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions` | `qwen-plus` |
| GLM / Zhipu | `https://open.bigmodel.cn/api/paas/v4/chat/completions` | `glm-4.7` |
| Local model | `http://127.0.0.1:11434/v1/chat/completions` or `http://127.0.0.1:8000/v1/chat/completions` | Use your local model name |

If your provider only gives you a `base_url` ending in `/v1`, you can enter that too. The app will append `/chat/completions` automatically. Real API keys are saved in local `user_settings.json`, which is ignored by Git.

### 3. Select an Audio Device
The app is designed to capture the audio that Windows is playing through your speakers, headphones, HDMI output, USB sound card, or virtual cable.

In the overlay settings, prefer devices labeled `[System Audio]` or `Loopback`, especially the one matching your current Windows playback device. Do not choose a normal microphone unless you intentionally want room audio; microphones usually cannot capture game voice playback directly.

If you only see microphones:
```bash
pip install PyAudioWPatch==0.2.12.8
```

You can also use VB-Cable as a fallback:
1. Download: <https://vb-audio.com/Cable/>
2. Set Windows default playback output to VB-Cable.
3. Select the matching VB-Cable capture/loopback device in the overlay.

### 4. Start the App
Double-click `run.bat`, or run:
```bash
python main.py
```

## Usage

### Overlay Controls
- **Ctrl+Shift+T**: Show/hide overlay
- **Ctrl+Alt+C**: Clear translation history
- **Ctrl+Alt+S**: Pause/resume translation
- **Drag overlay**: Move the overlay window
- **Gear button**: Configure API Key, model name, endpoint, audio device, opacity, colors, and hotkeys

### Translation Direction
The app automatically detects whether recognized text is Chinese or English:
- English speech is translated to Chinese.
- Chinese speech is translated to English.
- Mixed Chinese/English text is handled by the dominant detected language while preserving game terms, acronyms, names, and locations where possible.

### Status And Error Messages
Important user-facing messages are shown in the overlay, including:
- Startup progress and Whisper model loading
- Selected system-audio / Loopback device
- Audio capture startup or device enumeration errors
- Pause/resume, clear history, and hotkey events
- Translation API timeout, provider HTTP status code, and provider error message

### Mobile View
1. Keep your PC and phone on the same LAN.
2. Open `http://PC_IP:8765/mobile` on your phone.
3. The phone receives translation results in real time.

## Configuration
Edit `config.json` or use the overlay settings:

| Key | Description |
|-----|-------------|
| `whisper.model_size` | Whisper model size: tiny/base/small/medium |
| `overlay.text_color` | Overlay text color |
| `audio.sample_rate` | Audio sample rate |
| `audio.max_speech_seconds` | Maximum seconds before forced splitting during continuous sound, recommended 6-10 |
| `translation.api_key` | OpenAI-compatible API Key |
| `translation.model` | Model name, default `Qwen/Qwen2.5-7B-Instruct` |
| `translation.endpoint` | Compatible endpoint or `/v1` base URL |
| `translation.temperature` | Translation creativity |

## Troubleshooting

### Cannot Capture Audio
- Choose `[System Audio]` / `Loopback`, not a normal microphone.
- Run `python list_devices.py` and confirm that system-audio devices are visible.
- Make sure the game sound is playing through the same speaker/headphone device you selected.
- If you use Bluetooth, HDMI, or a USB sound card, choose the matching system-audio/loopback item.
- Re-run `install.bat` or install `PyAudioWPatch==0.2.12.8`.
- Try VB-Cable if your device driver does not expose loopback capture.
- Try running as administrator.

### Translation Fails
- Check the overlay message for API status code and provider error details.
- Confirm API Key, model name, and endpoint in the gear settings.
- If using a local model, make sure the endpoint is reachable and compatible with Chat Completions.
- Increase `translation.timeout_seconds` if the provider is slow.

### Recognition Is Inaccurate
- Increase `whisper.model_size`.
- Lower background music volume.
- Ensure the selected audio device is the one actually playing game voice.

## Scope
This tool captures Windows system playback audio. It is not hard-coded for specific games and should not claim individual game compatibility without testing. If the game voice is audible through the selected playback device and Windows exposes a matching system-audio/loopback capture device, it can usually be tried.

Some games, anti-cheat systems, exclusive audio mode, remote streaming tools, DRM protection, or special sound drivers may block capture. Use another output device, disable exclusive mode, or route audio through VB-Cable when needed.

## Notes
1. The first run may download the Whisper model.
2. Translation requires network access unless you use a local model.
3. Start this app before joining a game voice session.
4. Keep the mobile page open if you use mobile mirroring.

## License
This community edition is licensed under the GNU General Public License v3.0. See [LICENSE](LICENSE).

Closed-source commercial use, private custom distribution, or commercial edition licensing requires separate authorization.
