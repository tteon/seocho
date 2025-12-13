
import yaml
import os
from neo4j import GraphDatabase

class SchemaManager:
    def __init__(self, uri=None, user=None, password=None):
        self.uri = uri or os.getenv("NEO4J_URI", "bolt://neo4j:7687")
        self.user = user or os.getenv("NEO4J_USER", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD", "password")
        self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))

    def close(self):
        self.driver.close()

    def apply_schema(self, database, yaml_path):
        """
        Reads a YAML schema definition and applies constraints/indexes.
        """
        print(f"📖 Reading schema from {yaml_path} for DB '{database}'...")
        
        if not os.path.exists(yaml_path):
            print(f"⚠️ Schema file {yaml_path} not found. Skipping.")
            return

        with open(yaml_path, 'r') as f:
            schema = yaml.safe_load(f)

        nodes = schema.get('nodes', {})
        
        with self.driver.session(database=database) as session:
            for label, definition in nodes.items():
                props = definition.get('properties', {})
                for prop_name, config in props.items():
                    # 1. Unique Constraints
                    if config.get('constraint') == 'UNIQUE':
                        constraint_name = f"constraint_{label}_{prop_name}_unique"
                        query = f"CREATE CONSTRAINT {constraint_name} IF NOT EXISTS FOR (n:{label}) REQUIRE n.{prop_name} IS UNIQUE"
                        try:
                            session.run(query)
                            print(f"   ✅ Applied UNIQUE constraint on :{label}({prop_name})")
                        except Exception as e:
                            print(f"   ❌ Failed to apply constraint on :{label}({prop_name}): {e}")

                    # 2. Indexes
                    if config.get('index') is True:
                        index_name = f"index_{label}_{prop_name}"
                        query = f"CREATE INDEX {index_name} IF NOT EXISTS FOR (n:{label}) ON (n.{prop_name})"
                        try:
                            session.run(query)
                            print(f"   ✅ Applied INDEX on :{label}({prop_name})")
                        except Exception as e:
                            print(f"   ❌ Failed to apply index on :{label}({prop_name}): {e}")

        print(f"✨ Schema application for '{database}' complete.")

    def update_schema_from_records(self, records: dict, yaml_path: str):
        """
        Scans extracted records (nodes/relationships) and updates the YAML schema.
        """
        print(f"🔍 Scanning records for schema updates in {yaml_path}...")
        
        if not os.path.exists(yaml_path):
            print(f"⚠️ Schema file {yaml_path} not found. Creating new.")
            current_schema = {"graph_type": "AutoDiscovered", "version": "1.0", "nodes": {}, "relationships": {}}
        else:
            with open(yaml_path, 'r') as f:
                current_schema = yaml.safe_load(f) or {}

        nodes_config = current_schema.get("nodes", {})
        rels_config = current_schema.get("relationships", {})

        # 1. Discover Nodes
        for node in records.get("nodes", []):
            label = node.get("label", "Unknown")
            if label not in nodes_config:
                print(f"   🆕 Discovered new Node Label: {label}")
                nodes_config[label] = {
                    "description": f"Auto-discovered node type: {label}",
                    "properties": {
                        "id": {"type": "STRING", "constraint": "UNIQUE"},
                        "name": {"type": "STRING", "index": "TRUE"}
                    }
                }
            # (Optional: Scan properties to add them too)

        # 2. Discover Relationships
        for rel in records.get("relationships", []):
            rel_type = rel.get("type", "RELATED_TO")
            if rel_type not in rels_config:
                print(f"   🆕 Discovered new Relationship Type: {rel_type}")
                rels_config[rel_type] = {
                    "source": "Any", # Hard to infer strict types without more analysis
                    "target": "Any"
                }

        # 3. Save Updates
        current_schema["nodes"] = nodes_config
        current_schema["relationships"] = rels_config

        with open(yaml_path, 'w') as f:
            yaml.dump(current_schema, f, sort_keys=False)
        
        print("✅ Schema YAML updated.")
