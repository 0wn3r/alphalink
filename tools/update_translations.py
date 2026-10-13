#!/usr/bin/env python3
"""Regenerate app/po/alphalink-cam.pot from the sources, rebuild app/po/uk.po from app/po/uk_translations.py, and
compile the catalogs into app/alphacam_gtk/locale/<lang>/LC_MESSAGES/alphalink-cam.mo.
Needs xgettext and msgfmt (gettext). Run from anywhere: python3 tools/update_translations.py"""

import re
import runpy
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
PO = APP / "po"
PLURAL = {"uk": "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);"}


def quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def main() -> int:
    sources = sorted(str(p.relative_to(APP)) for d in ("alphacam", "alphacam_gtk") for p in (APP / d).rglob("*.py"))
    (PO / "POTFILES").write_text("\n".join(sources) + "\n")
    subprocess.run(["xgettext", "--from-code=UTF-8", "--language=Python", "--keyword=_", "--keyword=N_",
                    "--keyword=ngettext:1,2", "--keyword=tr", "--add-comments=TRANSLATORS", "--package-name=alphalink-cam",
                    "--package-version=0.1.0", "--sort-by-file", "-f", "po/POTFILES", "-o", "po/alphalink-cam.pot"],
                   cwd=APP, check=True)
    pot = (PO / "alphalink-cam.pot").read_text()
    ids = re.findall(r'\nmsgid ((?:".*"\n)+)(?:msgid_plural ((?:".*"\n)+))?', pot)

    def unq(block):
        return "".join(re.findall(r'"(.*)"', block)).encode().decode("unicode_escape").encode("latin1").decode()

    missing = []
    for lang in ("uk",):
        table = runpy.run_path(str(PO / f"{lang}_translations.py"))[lang.upper()]
        out = [f'msgid ""\nmsgstr ""\n"Project-Id-Version: alphalink-cam 0.1.0\\n"\n"Language: {lang}\\n"\n'
               f'"PO-Revision-Date: 2026-10-13 12:00+0300\\n"\n"Last-Translator: Claude\\n"\n'
               f'"Language-Team: Ukrainian\\n"\n'
               f'"MIME-Version: 1.0\\n"\n"Content-Type: text/plain; charset=UTF-8\\n"\n'
               f'"Content-Transfer-Encoding: 8bit\\n"\n"Plural-Forms: {PLURAL[lang]}\\n"\n']
        for m, p in ids:
            mid, mpl = unq(m), unq(p) if p else None
            if not mid:
                continue
            if mpl is None:
                tr = table.get(mid)
                if tr is None:
                    missing.append(mid)
                    tr = ""
                out.append(f"msgid {quote(mid)}\nmsgstr {quote(tr)}\n")
            else:
                forms = table.get((mid, mpl))
                if forms is None:
                    missing.append(mid)
                    forms = [""] * 3
                lines = [f"msgid {quote(mid)}", f"msgid_plural {quote(mpl)}"]
                lines += [f"msgstr[{i}] {quote(f)}" for i, f in enumerate(forms)]
                out.append("\n".join(lines) + "\n")
        (PO / f"{lang}.po").write_text("\n".join(out))
        mo = APP / "alphacam_gtk" / "locale" / lang / "LC_MESSAGES" / "alphalink-cam.mo"
        mo.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["msgfmt", "--check", "-o", str(mo), str(PO / f"{lang}.po")], check=True)
    if missing:
        print("untranslated:", *missing, sep="\n  ", file=sys.stderr)
        return 1
    print("translations up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
