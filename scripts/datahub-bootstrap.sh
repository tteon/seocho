#!/bin/bash

echo "[Seocho] Installing DataHub CLI if not present..."

if ! command -v datahub &> /dev/null; then
  pip install --upgrade acryl-datahub
else
  echo "DataHub CLI already installed."
fi

echo "[Seocho] Creating directories..."

mkdir -p sharepoint/input
mkdir -p sharepoint/output

echo "[Seocho] Setup complete."
