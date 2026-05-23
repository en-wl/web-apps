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

systemctl start wl-web-app-create wl-web-app-speller-lookup || true

sleep 5

ok=1
for svc in wl-web-app-create wl-web-app-speller-lookup; do
  state=$(systemctl show -p ActiveState --value "$svc")
  if [ "$state" != "active" ]; then
    echo "*** $svc not active after start: ActiveState=$state"
    ok=0
  fi
done

if [ "$ok" -ne 1 ]; then
  echo "*** rolling back to previous version"
  systemctl stop wl-web-app-create wl-web-app-speller-lookup || true
  mv git git-new
  mv git-old git
  systemctl start wl-web-app-create wl-web-app-speller-lookup
  exit 1
fi

echo "*** done"

