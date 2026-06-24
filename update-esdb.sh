#!/bin/sh

set -ex

GIT_UPDATE="git merge --ff-only"

if [ "$1" = "--force" ]; then
  GIT_UPDATE="git reset --hard"
fi

cd scowl
UPSTREAM="$(git rev-parse --abbrev-ref @{upstream} 2>/dev/null || echo origin/v2)"
git clean -q -f -x -d
git fetch
$GIT_UPDATE "$UPSTREAM"
make scowl.db
cd ..

cd diff-code
git clean -q -f -x -d
cd ..

cd diff
git clean -q -f -x -d
git fetch
$GIT_UPDATE origin/diff
cd ..

./test_create.py -q

./make_dicts_table.py

chmod 644 history.db
diff-code/util/track-words.py --force diff/ history.db
chmod 444 history.db
