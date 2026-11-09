#!/bin/sh
set -eu

if [ "$#" -gt 2 ] || [ "${1:-}" != --kubernetes ]; then
    echo "Usage: $0 --kubernetes [deploy|undeploy]" >&2
    exit 2
fi
cd -- "$(dirname -- "$0")"
exec sh .ci/cluster.sh "${2:-deploy}"
