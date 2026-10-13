"""Translation hook for messages the core shows to people (errors that the app puts in its banner or toasts).

The core stays free of GTK and locale files: messages are written in English with `tr("... {name} ...", name=...)`,
and the GTK app installs its gettext function with `set_translator` (alphacam_gtk.i18n), so the same catalog
(domain "alphalink-cam") translates them. Without a translator the English text is used, e.g. on the CLI."""

from __future__ import annotations

_translate = None


def set_translator(fn) -> None:
    global _translate
    _translate = fn


def tr(message: str, **values) -> str:
    text = _translate(message) if _translate else message
    return text.format(**values) if values else text
