#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x 'build/Virtua Tennis.app/Contents/MacOS/VirtuaTennis' ]]; then
    ./build.sh
fi
exec /usr/bin/open "$PWD/build/Virtua Tennis.app"
