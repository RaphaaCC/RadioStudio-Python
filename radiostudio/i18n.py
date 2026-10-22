"""Locale selection and explicit catalog lookups for the user interface."""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from PySide6.QtCore import QLocale

SUPPORTED_LANGUAGES = (
    ("Português (Brasil)", "pt_BR"),
    ("English (United States)", "en_US"),
    ("Español (México)", "es_MX"),
    ("한국어", "ko_KR"),
    ("日本語", "ja_JP"),
    ("繁體中文", "zh_TW"),
    ("简体中文", "zh_CN"),
)
LANGUAGE_CHOICES = (("Automático (idioma do sistema)", "system"),) + SUPPORTED_LANGUAGES

_SYSTEM_LOCALE_MAP = {
    "pt": "pt_BR", "en": "en_US", "es": "es_MX", "ko": "ko_KR",
    "ja": "ja_JP", "zh_TW": "zh_TW", "zh_HK": "zh_TW", "zh_MO": "zh_TW",
    "zh": "zh_CN",
}
_locale = "pt_BR"
_preference = "system"


def normalize_language(value: Any) -> str | None:
    """Return a supported preference or ``None`` for invalid saved data."""
    if not isinstance(value, str):
        return None
    value = value.strip().replace("-", "_")
    if value == "system" or value in {locale for _, locale in SUPPORTED_LANGUAGES}:
        return value
    return None


def system_language() -> str:
    """Map the operating-system locale to the closest supported language."""
    try:
        name = QLocale.system().name().replace("-", "_")
    except Exception:
        name = "en_US"
    normalized = name if name in _SYSTEM_LOCALE_MAP else name.split("_", 1)[0]
    return _SYSTEM_LOCALE_MAP.get(normalized, "en_US")


def resolve_language(preference: str | None) -> str:
    normalized = normalize_language(preference) or "system"
    return system_language() if normalized == "system" else normalized


def current_language() -> str:
    return _locale


def current_preference() -> str:
    return _preference


def set_language(preference: str) -> str:
    """Set the process language and return its resolved locale."""
    global _locale, _preference
    _preference = normalize_language(preference) or "system"
    _locale = resolve_language(_preference)
    return _locale


@lru_cache(maxsize=len(SUPPORTED_LANGUAGES))
def _load_catalog(locale: str) -> dict[str, str]:
    """Load one complete language catalog from its locale-specific JSON file."""
    if locale not in {code for _, code in SUPPORTED_LANGUAGES}:
        return {}
    path = Path(__file__).parent / "translations" / f"{locale}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("O catálogo precisa ser um objeto JSON")
        if not all(isinstance(source, str) and isinstance(target, str)
                   for source, target in data.items()):
            raise ValueError("O catálogo contém chaves ou traduções inválidas")
        return data
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        logging.getLogger("radiostudio").exception(
            "Não foi possível carregar o catálogo de idioma %s", locale)
        return {}


@lru_cache(maxsize=len(SUPPORTED_LANGUAGES))
def _template_patterns(locale: str) -> tuple[tuple[re.Pattern[str], str, tuple[str, ...]], ...]:
    patterns = []
    for source, target in _load_catalog(locale).items():
        if "{" not in source or "}" not in source:
            continue
        parts: list[str] = []
        cursor = 0
        names: list[str] = []
        for match in re.finditer(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", source):
            parts.append(re.escape(source[cursor:match.start()]))
            names.append(match.group(1))
            parts.append(f"(?P<slot{len(names) - 1}>.*?)")
            cursor = match.end()
        parts.append(re.escape(source[cursor:]))
        try:
            patterns.append((re.compile("^" + "".join(parts) + "$", re.DOTALL),
                             target, tuple(names),))
        except re.error:
            logging.getLogger("radiostudio").warning(
                "Modelo de tradução inválido: %r", source)
    # Prefer messages with more fixed text over generic templates.
    patterns.sort(key=lambda entry: len(entry[0].pattern), reverse=True)
    return tuple(patterns)


def translate(text: Any, locale: str | None = None) -> Any:
    """Look up one interface string in the selected locale catalog."""
    if not isinstance(text, str) or not text:
        return text
    target_locale = locale or _locale
    if target_locale == "system":
        target_locale = system_language()
    table = _load_catalog(target_locale)
    translated = table.get(text)
    if translated is not None:
        return translated
    for pattern, template, names in _template_patterns(target_locale):
        match = pattern.fullmatch(text)
        if match:
            for index, name in enumerate(names):
                template = template.replace(
                    "{" + name + "}", match.groupdict().get(f"slot{index}", ""))
            return template
    return text


_BUTTON_TEXT_SOURCE = {
    "OK": "OK", "Cancel": "Cancelar", "Apply": "Aplicar", "Close": "Fechar",
    "Yes": "Sim", "No": "Não", "Save": "Salvar", "Open": "Abrir",
    "Discard": "Descartar", "Retry": "Tentar novamente", "Ignore": "Ignorar",
    "Abort": "Cancelar", "Reset": "Redefinir", "Restore Defaults": "Restaurar padrões",
    "Help": "Ajuda",
}


def translate_dialog_buttons(button_box, locale: str | None = None) -> None:
    """Translate standard Qt button captions from the selected language file."""
    for button in button_box.buttons():
        caption = button.text().replace("&", "")
        source = _BUTTON_TEXT_SOURCE.get(caption, caption)
        button.setText(translate(source, locale))


def read_language_preference(
    state_file: Path, preference_file: Path, installer_file: Path
) -> tuple[str, bool]:
    """Read language preference without modifying or replacing a damaged profile."""
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        if isinstance(state, dict):
            value = normalize_language(state.get("language_preference"))
            if value is not None:
                return value, True
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    for path in (preference_file, installer_file):
        try:
            value = normalize_language(path.read_text(encoding="utf-8").strip())
            if value is not None:
                return value, True
        except OSError:
            continue
    return "system", False


def save_language_preference(path: Path, preference: str) -> None:
    value = normalize_language(preference) or "system"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(value + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            logging.getLogger("radiostudio").warning(
                "Não foi possível remover o arquivo temporário de idioma")


def install_translations(_app, preference: str) -> str:
    """Select and load the locale catalog before constructing the interface."""
    resolved = set_language(preference)
    _load_catalog(resolved)
    return resolved
