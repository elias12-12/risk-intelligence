#!/bin/sh
# Reconcile node_modules with the lockfile, then run whatever was asked for.
#
# node_modules lives in a named volume rather than in the image or on the host:
# the bind mount over /app would otherwise shadow the image's copy with a
# directory the host does not have, and putting it on the host is the one thing
# this whole arrangement exists to avoid.
#
# Docker seeds that volume from the image the FIRST time it is used and never
# again. So a rebuilt image whose package-lock.json moved would be silently
# shadowed by the old dependencies, and the first symptom would be a test
# failing for a reason that is not in the diff. Compare the lockfile against the
# stamp the build wrote and reinstall when the two disagree.
set -e

STAMP=/app/node_modules/.lockstamp

if [ ! -f "$STAMP" ] || ! md5sum -c --status "$STAMP" 2>/dev/null; then
    if [ -f "$STAMP" ]; then
        echo "console: package-lock.json has moved since node_modules was built."
    else
        echo "console: no dependencies in the volume yet."
    fi
    echo "console: running npm ci (this is the only install, and it is not on your machine)"
    npm ci
    # `npm ci` removes node_modules wholesale, stamp included, so it is written
    # back afterwards rather than kept.
    md5sum package-lock.json > "$STAMP"
fi

exec "$@"
