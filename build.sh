#!/bin/sh
set -eu

cd -- "$(dirname -- "$0")"
image_tag=${1:-triton/vectorcraft:local}
if [ "$#" -gt 0 ]; then
    shift
fi
exec docker build --file Dockerfile --tag "$image_tag" "$@" .
