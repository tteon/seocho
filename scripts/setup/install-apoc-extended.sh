#!/bin/bash
# Install version-pinned APOC Extended plus Parquet/Hadoop dependencies.
set -euo pipefail

APOC_EXTENDED_VERSION="${APOC_EXTENDED_VERSION:-5.26.4}"
BASE_URL="https://github.com/neo4j-contrib/neo4j-apoc-procedures/releases/download/${APOC_EXTENDED_VERSION}"
TARGET="${SEOCHO_NEO4J_PLUGIN_DIR:-data/neo4j/plugins}"
mkdir -p "$TARGET"

download() {
    name="$1"
    destination="$TARGET/$name"
    if [ -s "$destination" ]; then
        echo "✓ $name already present"
        return
    fi
    temporary="${destination}.tmp"
    echo "Downloading $name ..."
    curl -fsSL --retry 3 --retry-all-errors -o "$temporary" "$BASE_URL/$name"
    mv "$temporary" "$destination"
}

download "apoc-${APOC_EXTENDED_VERSION}-extended.jar"
download "apoc-hadoop-dependencies-${APOC_EXTENDED_VERSION}-all.jar"

echo "APOC Extended ${APOC_EXTENDED_VERSION} installed in $TARGET"
