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

The plumbing is in place and the first-run wizard + page titles are wrapped.
Wrapping the rest of the UI strings is a welcome contribution — grep for
un-wrapped `label=`, `title=`, `subtitle=`, `set_label(` in `src/goblinmode/gui/`.

`.po` files ship in the repo; `.mo` files are built at install time (or by the
extract script) and are **not** committed.
