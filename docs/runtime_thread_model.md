# VoxGo runtime thread model

This document records the intended runtime boundaries for VoxGo. `main.py` is a
compatibility launcher; the application coordinator now lives in `voxgo/app.py`.
Keep behavior stable while moving each concern toward one owner and communicating
across owners with queues, scheduled coroutines, Qt signals, or runtime events.

## Owners

### Qt main thread

Owns:

- `QApplication`, overlay window, tray icon, settings and setup wizard.
- User-visible notices, dialogs, QR widget, and overlay state.
- The timer that calls `VoxGoApp._process_audio_tick`.

Rules:

- Worker threads must not update Qt widgets directly.
- Cross-thread UI updates should be delivered through Qt signals or a main-thread
  dispatch helper.

### Audio capture

Owns:

- WASAPI loopback stream selection and audio buffering in `SystemAudioCapture`.
- Speech segment detection and the callback into `VoxGoApp._on_speech_detected`.
- Setup audio test monitoring in `AudioLevelMonitor` and its
  `audio-level-monitor` thread.

Rules:

- Audio code emits `SpeechSegment` data only.
- It does not call ASR, translation, overlay, or mobile services directly.

### ASR worker thread

Current thread name: `speech-worker`.

Owns:

- Reading `SpeechWorkItem` objects from `_speech_queue`.
- Calling `SpeechRecognizer.transcribe_audio_bytes_with_language`.
- Filtering transcription results and creating translation item ids.

Rules:

- Input is only `queue.Queue`.
- Output should become an app event such as `TranscriptReady`.
- ASR code should not directly own translation, mobile, or Qt behavior.

### Translation asyncio loop

Current owner: `_translation_loop` in `_translation_thread`.

Owns:

- Async translator calls through `GameTranslator`.
- `aiohttp.ClientSession` lifecycle, including close on shutdown.
- Translation concurrency limits.

Rules:

- Schedule work with `asyncio.run_coroutine_threadsafe`.
- Create and close async resources on the same event loop.
- Output should become an app event such as `TranslationReady`.

### Mobile server thread

Current thread name: `mobile-server`.

Owns:

- FastAPI and uvicorn server lifecycle.
- WebSocket connections and broadcast fan-out.
- Static mobile page assets under `voxgo/mobile/static`.

Rules:

- Schedule broadcasts onto `_mobile_loop` with `asyncio.run_coroutine_threadsafe`.
- Mobile handlers should not call overlay, ASR, or translation services directly.

### Startup and update workers

Current thread names include `startup-loader` and update-check worker threads.

Owns:

- Lazy initialization of Whisper and translator services.
- Update manifest checks.
- Model download progress reporting.

Rules:

- Report progress through notices or app events.
- Do not mutate Qt widgets directly from these workers.

## Cross-owner messages

The next structural refactor should introduce explicit messages before moving
large chunks of `VoxGoApp`:

```python
@dataclass
class TranscriptReady:
    text: str
    language: str
    trace_id: str


@dataclass
class TranslationReady:
    original: str
    translated: str
    source_lang: str
    target_lang: str
    trace_id: str


@dataclass
class AppNotice:
    level: str
    title: str
    message: str
```

Initial consumers:

- Overlay UI receives `TranslationReady` and `AppNotice`.
- Mobile server receives `TranslationReady`.
- Diagnostics and latency tracking receive `TranscriptReady` and
  `TranslationReady`.

## Shutdown order

The safe shutdown order is:

1. Stop new audio ticks and mark the app as stopping.
2. Stop audio capture.
3. Drain and stop `speech-worker`.
4. Remove global hotkeys.
5. Stop mobile WebSocket connections on `_mobile_loop`.
6. Close translator resources on `_translation_loop`.
7. Stop and close `_translation_loop`.
8. Close overlay and tray UI.

When a worker is still starting or processing, cleanup may be skipped to avoid
destroying resources from the wrong thread. Keep that behavior explicit and
logged.
