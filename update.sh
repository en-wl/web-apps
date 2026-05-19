#!/bin/sh

# update the web-app(s) on the server side

set -e

echo "*** updating"

cd /opt/wl-web-app
if [ -d git-old ]; then
  rm -rf git-new
  mv git-old git-new
fi

rsync -aH --delete --exclude=/scowl/scowl.db --exclude=__pycache__ git/ git-new/

cd git-new
git fetch --recurse-submodules origin deploy
git reset --recurse-submodules --hard origin/deploy
git clean -q -f -x -d -e /history.db
sh update-esdb.sh

echo "*** copying into place and restarting web-app(s)"

cd /opt/wl-web-app

systemctl stop wl-web-app-create wl-web-app-speller-lookup

rm -rf git-old
mv git git-old
mv git-new git

systemctl start wl-web-app-create wl-web-app-speller-lookup

echo "*** done"

