#!/bin/bash
# Install OpenGDS — DozerDB's distribution of Neo4j Graph Data Science.
#
# The vanilla Neo4j docker plugin manifest pulls GDS *Enterprise*, which
# requires com.neo4j.metrics.MetricsManager (Enterprise-only) and crashes
# on DozerDB Community. OpenGDS is the Community-compatible drop-in.
#
# Drops the jar into both the main stack's plugins dir
# (data/neo4j/plugins) and the tutorial bundle's plugins dir
# (.seocho/tutorials-neo4j/plugins) so either compose can pick it up.
#
# Override the version with:
#   OPEN_GDS_VERSION=2.13.0 scripts/setup/install-opengds.sh
set -euo pipefail

GDS_VERSION="${OPEN_GDS_VERSION:-2.12.0}"
GDS_URL="https://dist.dozerdb.org/plugins/open-gds/open-gds-${GDS_VERSION}.jar"

for target in data/neo4j/plugins .seocho/tutorials-neo4j/plugins; do
    mkdir -p "$target"
    jar_path="$target/open-gds-${GDS_VERSION}.jar"
    if [ -f "$jar_path" ]; then
        echo "✓ OpenGDS ${GDS_VERSION} already present in $target"
        continue
    fi
    echo "Downloading OpenGDS ${GDS_VERSION} -> $target ..."
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --retry 3 -o "$jar_path" "$GDS_URL"
    elif command -v wget >/dev/null 2>&1; then
        wget --quiet --tries=3 -O "$jar_path" "$GDS_URL"
    else
        echo "ERROR: need curl or wget to download OpenGDS." >&2
        exit 1
    fi
    echo "  done."
done

echo
echo "OpenGDS ${GDS_VERSION} installed. Restart your Neo4j container so it"
echo "picks up the new plugin:"
echo "  make restart                                # main stack"
echo "  docker compose -f docker-compose.tutorials.yml restart tutorials-neo4j"
