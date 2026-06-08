# Diagnostics

These scripts are manual troubleshooting tools. They are not imported by the app
and are not part of the portable release build.

Run them from the project root with the same Python environment used by the app:

```bash
python diagnostics/check_imports.py
python diagnostics/list_devices.py
python diagnostics/check_stereo_mix.py
python diagnostics/check_whisper.py
python diagnostics/diagnose_startup.py
python diagnostics/generate_mobile_qr.py
python diagnostics/check_openai_compatible_api.py
python diagnostics/check_bidirectional_translation.py
python diagnostics/check_translation_loop.py
python diagnostics/benchmark_siliconflow_models.py
python diagnostics/show_minimal_overlay.py
```

Notes:
- API checks read `config.json` and require a real API key unless the endpoint is local.
- `benchmark_siliconflow_models.py` is only for SiliconFlow/OpenAI-compatible model comparison.
- `generate_mobile_qr.py` auto-detects the LAN IP by default. Override it with `--url` if needed.
