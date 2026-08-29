#!/usr/bin/env bash
# Regenerate po/goblin-mode-pro.pot from the marked strings ( _(...) / ngettext ).
# Then: msginit -l <lang> -i po/goblin-mode-pro.pot -o po/<lang>.po   to start a language,
#       msgmerge -U po/<lang>.po po/goblin-mode-pro.pot                to refresh one.
set -euo pipefail
cd "$(dirname "$0")/.."

xgettext \
    --language=Python \
    --keyword=_ --keyword=ngettext:1,2 \
    --from-code=UTF-8 \
    --package-name="Goblin Mode Pro" \
    --msgid-bugs-address="https://github.com/Bvaughan7/goblin-mode-pro/issues" \
    --output=po/goblin-mode-pro.pot \
    $(git ls-files 'src/goblinmode/*.py')

echo "wrote po/goblin-mode-pro.pot"

# compile any existing catalogues into po/_build/<lang>/LC_MESSAGES/ so a source
# checkout can be tested with GOBLIN_LOCALEDIR=$PWD/po/_build
for po in po/*.po; do
    [ -e "$po" ] || continue
    lang=$(basename "$po" .po)
    mkdir -p "po/_build/$lang/LC_MESSAGES"
    msgfmt "$po" -o "po/_build/$lang/LC_MESSAGES/goblin-mode-pro.mo"
    echo "compiled $lang"
done
