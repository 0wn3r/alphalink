"""Translations (gettext, domain "alphalink-cam"). The language follows the desktop (LANGUAGE / LC_MESSAGES / LANG);
`ALPHALINK_LANG=uk` overrides it. Compiled catalogs live in alphacam_gtk/locale/<lang>/LC_MESSAGES/."""

from __future__ import annotations

import gettext
import locale
import os
from pathlib import Path

DOMAIN = "alphalink-cam"
LOCALE_DIR = Path(__file__).with_name("locale")


def _languages() -> list[str] | None:
    forced = os.environ.get("ALPHALINK_LANG")
    return [forced] if forced else None  # None: gettext reads LANGUAGE, LC_ALL, LC_MESSAGES, LANG


_t = gettext.translation(DOMAIN, LOCALE_DIR, languages=_languages(), fallback=True)
try:  # also for GtkBuilder / C libraries that use the domain
    locale.bindtextdomain(DOMAIN, str(LOCALE_DIR))
except (AttributeError, OSError):
    pass


def _(text: str) -> str:
    return _t.gettext(text)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _t.ngettext(singular, plural, n)


def N_(text: str) -> str:
    """Mark for extraction only; translate later with _()."""
    return text


def language() -> str:
    info = _t.info() if hasattr(_t, "info") else {}
    return info.get("language", "en") if isinstance(info, dict) else "en"
