#!/bin/sh

set -ex

GIT_UPDATE="git merge --ff-only"

if [ "$1" = "--force" ]; then
  GIT_UPDATE="git reset --hard"
fi

cd scowl
git clean -q -f -x -d
git fetch origin v2
$GIT_UPDATE origin/v2
make scowl.db
cd ..

./make_dicts_table.py
