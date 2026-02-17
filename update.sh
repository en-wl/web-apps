#!/bin/sh

# update the web-app(s) on the server side

set -e

echo "*** updating"

cd /opt/wl-web-app
rm -rf git-new
cp -a -n git git-new

cd git-new
git fetch --recurse-submodules origin main
git reset --recurse-submodules --hard origin/main
git clean -q -f -x -d
cd scowl
git clean -q -f -x -d
git fetch origin v2
git merge --ff-only origin/v2
make scowl.db

echo "*** copying into place and restarting web-app(s)"

cd /opt/wl-web-app
rm -rf git-old
systemctl stop wl-web-app-create
mv git git-old
mv git-new git
systemctl start wl-web-app-create

echo "*** done"
