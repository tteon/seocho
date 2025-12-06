#!/bin/bash

# Create plugins directory if it doesn't exist
mkdir -p data/neo4j/plugins

# Download OpenGDS
echo "Downloading OpenGDS 2.12.0..."
wget -O data/neo4j/plugins/open-gds-2.12.0.jar https://dist.dozerdb.org/plugins/open-gds/open-gds-2.12.0.jar

echo "OpenGDS downloaded successfully."
