#!/bin/sh
# Build the muOS Archive Manager package: dist/muOS-SP-Controls-<version>.muxzip
# "init" and "override" are the Archive Manager top-level folders that land in
# MUOS/init and MUOS/info/override respectively.
set -eu
cd "$(dirname "$0")"
VERSION="${1:-$(git describe --tags --always 2>/dev/null || echo dev)}"
OUT="dist/muOS-SP-Controls-$VERSION.muxzip"
mkdir -p dist; rm -f "$OUT"
(cd MUOS && zip -r -X -9 "../$OUT" init -x '*.DS_Store')
(cd MUOS/info && zip -r -X -9 "../../$OUT" override -x '*.DS_Store')
echo "built $OUT"
