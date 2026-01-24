#!/bin/bash
# Setup script for GraphRAG Environment Variables

echo "=============================================="
echo "   GraphRAG Environment Setup Assistant"
echo "=============================================="

ENV_FILE=".env"

if [ -f "$ENV_FILE" ]; then
    echo "Found existing .env file."
    read -p "Do you want to update it? (y/n): " update_env
    if [ "$update_env" != "y" ]; then
        echo "Exiting..."
        exit 0
    fi
else
    echo "Creating new .env file from .env.example..."
    if [ -f ".env.example" ]; then
        cp .env.example .env
    else
        echo "Error: .env.example not found!"
        exit 1
    fi
fi

# Function to prompt and update
update_var() {
    local var_name=$1
    local current_val=$(grep "^$var_name=" $ENV_FILE | cut -d'=' -f2-)
    
    echo ""
    echo "Current $var_name: ${current_val:-[Not Set]}"
    read -p "Enter new value for $var_name (or press Enter to keep): " new_val
    
    if [ ! -z "$new_val" ]; then
        # Escape special characters if needed, for simple keys it's usually fine
        # Using sed to replace or append
        if grep -q "^$var_name=" $ENV_FILE; then
            # Replace
            # Use | as delimiter to avoid issues with / in urls/keys
            sed -i "s|^$var_name=.*|$var_name=$new_val|" $ENV_FILE
        else
            # Append
            echo "$var_name=$new_val" >> $ENV_FILE
        fi
        echo "Updated $var_name"
    fi
}

# 1. OpenAI API Key
update_var "OPENAI_API_KEY"

# 2. HuggingFace Token
update_var "HF_TOKEN"

# 3. Neo4j Settings (Optional)
echo ""
read -p "Do you want to configure Neo4j settings? (y/n): " config_neo4j
if [ "$config_neo4j" == "y" ]; then
    update_var "NEO4J_URI"
    update_var "NEO4J_USER"
    update_var "NEO4J_PASSWORD"
fi

# 4. GitHub Token (for pushing changes)
update_var "GITHUB_TOKEN"

echo ""
echo "=============================================="
echo "   Setup Complete! .env file updated."
echo "=============================================="
