"""Internationalization (i18n) module for LibreStreamer.

Loads JSON translation files from the locales/ directory and provides
a translate() function for use in Jinja2 templates and Python code.

Language detection order:
  1. Cookie 'ls_lang'
  2. User preferences in DB
  3. Accept-Language HTTP header
  4. Default: 'en'
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

log = logging.getLogger("librestreamer.i18n")

_HERE = os.path.dirname(os.path.abspath(__file__))
_LOCALES_DIR = os.path.join(_HERE, "locales")
DEFAULT_LANG = "en"

_translations: dict[str, dict[str, str]] = {}
_available: list[str] = []

SUPPORTED = [
    ("en", "English"),
    ("es", "Español"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("it", "Italiano"),
    ("pt", "Português"),
    ("pt-BR", "Português (Brasil)"),
    ("ru", "Русский"),
    ("zh", "简体中文"),
    ("zh-TW", "繁體中文"),
    ("ja", "日本語"),
    ("ko", "한국어"),
    ("ar", "العربية"),
    ("hi", "हिन्दी"),
    ("tr", "Türkçe"),
    ("pl", "Polski"),
    ("nl", "Nederlands"),
    ("sv", "Svenska"),
    ("no", "Norsk"),
    ("da", "Dansk"),
    ("fi", "Suomi"),
    ("cs", "Čeština"),
    ("el", "Ελληνικά"),
    ("he", "עברית"),
    ("th", "ไทย"),
    ("vi", "Tiếng Việt"),
    ("id", "Bahasa Indonesia"),
    ("uk", "Українська"),
    ("ro", "Română"),
    ("hu", "Magyar"),
    ("ca", "Català"),
]


def load_translations() -> None:
    global _translations, _available
    _translations.clear()
    _available.clear()
    if not os.path.isdir(_LOCALES_DIR):
        log.warning("locales directory not found: %s", _LOCALES_DIR)
        return
    for fname in os.listdir(_LOCALES_DIR):
        if not fname.endswith(".json"):
            continue
        lang = fname[:-5]
        path = os.path.join(_LOCALES_DIR, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                _translations[lang] = json.load(f)
            _available.append(lang)
        except Exception as e:
            log.warning("failed to load locale %s: %s", lang, e)
    log.info("loaded %d locales: %s", len(_available), ", ".join(sorted(_available)))


def available_languages() -> list[tuple[str, str]]:
    return [(code, name) for code, name in SUPPORTED if code in _available]


def translate(lang: str, key: str, **kwargs: Any) -> str:
    if not _translations:
        load_translations()
    table = _translations.get(lang) or _translations.get(DEFAULT_LANG) or {}
    val = table.get(key)
    if val is None and lang != DEFAULT_LANG:
        val = (_translations.get(DEFAULT_LANG) or {}).get(key)
    if val is None:
        return key
    if kwargs:
        try:
            return val.format(**kwargs)
        except Exception:
            return val
    return val


def detect_language(cookie_lang: str | None = None, user_pref: str | None = None,
                     accept_lang: str | None = None) -> str:
    if cookie_lang and cookie_lang in _available:
        return cookie_lang
    if user_pref and user_pref in _available:
        return user_pref
    if accept_lang:
        for part in accept_lang.split(","):
            code = part.strip().split(";")[0].strip()
            if code in _available:
                return code
            short = code.split("-")[0]
            if short in _available:
                return short
    return DEFAULT_LANG
