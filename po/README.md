# Translations

Goblin Mode Pro uses gettext. Strings wrapped with `_( )` in
`src/goblinmode/**` are collected into `goblin-mode-pro.pot`.

## Add a language

```sh
./scripts/i18n-extract.sh                       # refresh the .pot
msginit -l pt_BR -i po/goblin-mode-pro.pot -o po/pt_BR.po
$EDITOR po/pt_BR.po                             # translate the msgstr lines
./scripts/i18n-extract.sh                       # compiles po/*.po → po/_build/
GOBLIN_LOCALEDIR="$PWD/po/_build" goblin-mode-pro   # test it
```

## Update an existing language after code changes

```sh
./scripts/i18n-extract.sh
msgmerge -U po/pt_BR.po po/goblin-mode-pro.pot
```

## Coverage

The UI is fully wrapped: every static, user-facing string across
`src/goblinmode/gui/` (wizard, dashboard, games, diagnostics, preflight and
the shared widgets) is marked with `_()`. Live-data strings built with
f-strings/`.format()`/`%` are intentionally left alone — gettext can't
translate those sanely without restructuring the call sites.

Five languages ship as real, hand-translated catalogues: German (`de`),
French (`fr`), Spanish (`es`), Brazilian Portuguese (`pt_BR`) and Simplified
Chinese (`zh_CN`). Adding another language or improving an existing
translation is still a welcome contribution — see "Add a language" above.

`.po` files ship in the repo; `.mo` files are built at install time (or by the
extract script) and are **not** committed.
