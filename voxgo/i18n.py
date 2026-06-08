UI_LANGUAGE_ZH = "zh-CN"
UI_LANGUAGE_EN = "en-US"

UI_LANGUAGE_OPTIONS = (
    (UI_LANGUAGE_ZH, "简体中文"),
    (UI_LANGUAGE_EN, "English"),
)

UI_LANGUAGE_LABELS = dict(UI_LANGUAGE_OPTIONS)


def normalize_ui_language(value: str) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    aliases = {
        "": UI_LANGUAGE_ZH,
        "zh": UI_LANGUAGE_ZH,
        "zh-cn": UI_LANGUAGE_ZH,
        "cn": UI_LANGUAGE_ZH,
        "chinese": UI_LANGUAGE_ZH,
        "simplified chinese": UI_LANGUAGE_ZH,
        "简体中文": UI_LANGUAGE_ZH,
        "中文": UI_LANGUAGE_ZH,
        "en": UI_LANGUAGE_EN,
        "en-us": UI_LANGUAGE_EN,
        "english": UI_LANGUAGE_EN,
        "us": UI_LANGUAGE_EN,
        "英文": UI_LANGUAGE_EN,
        "英语": UI_LANGUAGE_EN,
    }
    return aliases.get(text, UI_LANGUAGE_ZH)


def is_english_ui(language: str) -> bool:
    return normalize_ui_language(language) == UI_LANGUAGE_EN


def ui_text(language: str, zh: str, en: str) -> str:
    return en if is_english_ui(language) else zh


def language_label(code: str, ui_language: str = UI_LANGUAGE_ZH) -> str:
    labels = {
        UI_LANGUAGE_ZH: {"en": "英语", "zh": "中文"},
        UI_LANGUAGE_EN: {"en": "English", "zh": "Chinese"},
    }
    return labels.get(normalize_ui_language(ui_language), labels[UI_LANGUAGE_ZH]).get(code, code)
