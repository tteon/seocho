
import os
from neo4j import GraphDatabase

def create_databases(db_names):
    """
    Creates databases in Neo4j if they don't exist.
    Requires connection to the 'system' database.
    """
    uri = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "password")

    print(f"Connecting to {uri} to manage databases...")
    
    try:
        # Must connect to 'system' database to run administration commands
        driver = GraphDatabase.driver(uri, auth=(user, password))
        
        with driver.session(database="system") as session:
            for db_name in db_names:
                try:
                    print(f"Checking/Creating database: {db_name}")
                    # Enterprise or DozerDB feature
                    q = f"CREATE DATABASE {db_name} IF NOT EXISTS"
                    session.run(q)
                    print(f"✅ Database '{db_name}' ready.")
                except Exception as e:
                    print(f"❌ Failed to create '{db_name}': {e}")
                    print("   (Note: Multi-database is an Enterprise/DozerDB feature. Community Edition does not support this.)")
                    
        driver.close()
        
    except Exception as e:
        print(f"❌ Connection to system database failed: {e}")

if __name__ == "__main__":
    # Example usage based on user snippet
    # "BASELINE": "kgnormal", "FIBO": "kgfibo", "TRACING": "agent_traces"
    target_dbs = ["kgnormal", "kgfibo", "agent_traces"]
    create_databases(target_dbs)
    
    # --- LEX Schema Application ---
    from schema_manager import SchemaManager
    sm = SchemaManager()
    
    # Map DBs to Schema Files
    schema_map = {
        "agent_traces": "conf/schemas/tracing.yaml",
        "kgnormal": "conf/schemas/baseline.yaml",
        "kgfibo": "conf/schemas/baseline.yaml" # Assuming same schema for now
    }
    
    # Determine base path for configs
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    for db, schema_file in schema_map.items():
        full_path = os.path.join(base_dir, schema_file)
        sm.apply_schema(db, full_path)
        
    sm.close()
