#!/bin/sh

set -ex

GIT_UPDATE="git merge --ff-only"

if [ "$1" = "--force" ]; then
  GIT_UPDATE="git reset --hard"
fi

cd scowl
git clean -q -f -x -d
git fetch
$GIT_UPDATE origin/v2
make scowl.db
cd ..

./make_dicts_table.py

cd diff-code
git clean -q -f -x -d
cd ..

cd diff
git clean -q -f -x -d
git fetch
$GIT_UPDATE origin/diff
cd ..

chmod 644 history.db
diff-code/util/track-words.py --force diff/ history.db
chmod 444 history.db
