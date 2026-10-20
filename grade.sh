#!/usr/bin/env bash
#
# OS Lab2 grader
#   usage: ./grade.sh [-v]
#   GRADER_ONLY=2-2 ./grade.sh   runs only the checks of one step
#
# Builds your shell with a fixed gcc command (your Makefile is NOT used),
# then runs every test in a pseudo-terminal. The tests are fixed;
# run it as often as you like.
#
set -u
cd "$(dirname "$0")" || exit 1

OUT="$PWD/.grade"

die() { echo "grade.sh: $*" >&2; exit 1; }

[ "$(uname -s)" = "Linux" ] || die "the grader must run on Linux (Ubuntu VM / WSL2 / container)"
command -v gcc     >/dev/null || die "gcc not found (sudo apt install build-essential)"
command -v python3 >/dev/null || die "python3 not found (sudo apt install python3)"

rm -rf "$OUT" && mkdir -p "$OUT"

printf '  Building your shell ... '
if ! gcc -Wall -Iinclude src/*.c -o "$OUT/my_shell" 2> "$OUT/compile.log"; then
	echo "failed"
	echo
	cat "$OUT/compile.log"
	echo
	echo "  Score: 0 / 7 (compilation failed)"
	exit 1
fi
WARNINGS=$(grep -c "warning:" "$OUT/compile.log")
echo "ok ($WARNINGS compiler warnings)"
[ "$WARNINGS" -gt 0 ] && sed 's/^/    /' "$OUT/compile.log"

gcc -O2 -Wall -o "$OUT/probe" tests/probe.c || die "failed to build tests/probe.c"

CHECKSUM=$(cat grade.sh tests/driver.py tests/probe.c | sha256sum | cut -c1-12)
# The official value is written into README.md when the template is built.
# shellcheck disable=SC2016  # the backticks are literal Markdown, not a command
EXPECTED=$(sed -n 's/.*Grader checksum: `\([0-9a-f]\{12\}\)`.*/\1/p' README.md 2>/dev/null | head -n 1)

GRADER_EXPECTED_CHECKSUM="$EXPECTED" python3 tests/driver.py "$OUT/my_shell" "$OUT/probe" "$CHECKSUM" "$@"
