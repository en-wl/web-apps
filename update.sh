#!/bin/sh

# update the web-app(s) on the server side

set -e

echo "*** updating"

cd /opt/wl-web-app
rm -rf git-new
cp -a -n git git-new

cd git-new
git fetch --recurse-submodules origin deploy
git reset --recurse-submodules --hard origin/deploy
git clean -q -f -x -d
sh update-esdb.sh

echo "*** copying into place and restarting web-app(s)"

cd /opt/wl-web-app
rm -rf git-old

systemctl stop wl-web-app-create wl-web-app-speller-lookup

mv git git-old
mv git-new git

systemctl start wl-web-app-create wl-web-app-speller-lookup

echo "*** done"

