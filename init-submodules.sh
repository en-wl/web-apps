#!/bin/sh

set -e

git clone https://github.com/en-wl/wordlist.git --single-branch scowl
git clone https://github.com/en-wl/wordlist-diff.git --single-branch diff
git clone https://github.com/en-wl/wordlist-diff.git --single-branch -b code diff-code

git submodule absorbgitdirs

git submodule init
