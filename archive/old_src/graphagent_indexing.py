"""
GraphAgent Indexing: Load kgbuild-traces.json into Neo4j LPG and RDF databases.

This script creates two separate databases in Neo4j:
- LPG (Labeled Property Graph): Stores nodes and relationships with properties
- RDF: Stores RDF triples using the n10s (neosemantics) plugin

Features:
- Robust error handling with retries
- Transaction-based batch processing
- Progress tracking with tqdm
"""

import os
import json
import time
from typing import List, Dict, Any, Optional
from functools import wraps
from dotenv import load_dotenv
from neo4j import GraphDatabase, Driver
from neo4j.exceptions import ServiceUnavailable, TransientError, ClientError
from tqdm import tqdm

# Load environment variables
load_dotenv()

# ==========================================
# Configuration
# ==========================================
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://graphrag-neo4j:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
DATA_FILE = os.getenv("KGBUILD_EXPORT_PATH", "/workspace/export_opik/kgbuild_export.json")

LPG_DATABASE = "lpg"
RDF_DATABASE = "rdf"

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds
BATCH_SIZE = 100


# ==========================================
# Retry Decorator
# ==========================================
def with_retry(max_retries: int = MAX_RETRIES, delay: float = RETRY_DELAY):
    """Decorator to add retry logic to functions."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (ServiceUnavailable, TransientError) as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        print(f"⚠️ Attempt {attempt + 1} failed: {e}. Retrying in {delay}s...")
                        time.sleep(delay)
                    else:
                        print(f"❌ All {max_retries} attempts failed.")
            raise last_exception
        return wrapper
    return decorator


# ==========================================
# Database Manager
# ==========================================
class Neo4jGraphManager:
    """Manages Neo4j connections and database operations."""
    
    def __init__(self, uri: str, user: str, password: str):
        self.driver: Optional[Driver] = None
        self.uri = uri
        self.user = user
        self.password = password
    
    def connect(self) -> None:
        """Establish connection to Neo4j."""
        print(f"🔗 Connecting to Neo4j at {self.uri}...")
        self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
        self.driver.verify_connectivity()
        print("✅ Connected to Neo4j successfully.")
    
    def close(self) -> None:
        """Close the Neo4j connection."""
        if self.driver:
            self.driver.close()
            print("🔌 Neo4j connection closed.")
    
    @with_retry()
    def create_database(self, db_name: str) -> None:
        """Create a database if it doesn't exist."""
        with self.driver.session(database="system") as session:
            # Check if database exists
            result = session.run("SHOW DATABASES")
            existing_dbs = [record["name"] for record in result]
            
            if db_name in existing_dbs:
                print(f"📦 Database '{db_name}' already exists.")
            else:
                session.run(f"CREATE DATABASE {db_name} IF NOT EXISTS")
                print(f"✅ Database '{db_name}' created.")
                # Wait for database to be online
                time.sleep(2)
    
    @with_retry()
    def clear_database(self, db_name: str) -> None:
        """Clear all data from a database."""
        with self.driver.session(database=db_name) as session:
            session.run("MATCH (n) DETACH DELETE n")
            print(f"🧹 Cleared all data from '{db_name}'.")
    
    @with_retry()
    def setup_rdf_constraints(self, db_name: str) -> None:
        """Setup n10s (neosemantics) for RDF support."""
        with self.driver.session(database=db_name) as session:
            try:
                # Initialize n10s graph config
                session.run("CALL n10s.graphconfig.init()")
                print(f"✅ n10s initialized for database '{db_name}'.")
            except ClientError as e:
                if "already exists" in str(e).lower() or "already initialized" in str(e).lower():
                    print(f"📦 n10s already initialized for '{db_name}'.")
                else:
                    # n10s might not be available, use alternative approach
                    print(f"⚠️ n10s not available: {e}. Using property-based RDF storage.")
    
    @with_retry()
    def create_lpg_indexes(self, db_name: str) -> None:
        """Create indexes for LPG database."""
        with self.driver.session(database=db_name) as session:
            # Create index on node id property for faster lookups
            try:
                session.run("CREATE INDEX node_id_index IF NOT EXISTS FOR (n:Node) ON (n.id)")
                print(f"✅ Indexes created for '{db_name}'.")
            except ClientError as e:
                print(f"⚠️ Index creation note: {e}")

def sanitize_properties(properties: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize property keys and values for Neo4j compatibility."""
    sanitized = {}
    for key, value in properties.items():
        # Sanitize key: replace special characters
        clean_key = key.replace("&", "and").replace("-", "_").replace(" ", "_").replace("(", "").replace(")", "")
        
        # Handle nested structures by converting to JSON string
        if isinstance(value, (dict, list)):
            sanitized[clean_key] = json.dumps(value)
        else:
            sanitized[clean_key] = value
    
    return sanitized



# ==========================================
# Data Loaders
# ==========================================
class LPGLoader:
    """Loads LPG data into Neo4j."""
    
    def __init__(self, manager: Neo4jGraphManager, db_name: str):
        self.manager = manager
        self.db_name = db_name
    
    @with_retry()
    def load_nodes(self, nodes: List[Dict[str, Any]], trace_id: str) -> int:
        """Load nodes into the LPG database."""
        if not nodes:
            return 0
        
        with self.manager.driver.session(database=self.db_name) as session:
            count = 0
            for node in nodes:
                node_id = node.get("id", "")
                label = node.get("label", "Node")
                properties = node.get("properties", {})
                
                # Sanitize label (replace spaces, ensure valid Cypher label)
                label = label.replace(" ", "_").replace("-", "_")
                
                # Add trace_id to properties for traceability
                properties["_trace_id"] = trace_id
                properties["_node_id"] = node_id
                
                # Sanitize properties for Neo4j
                properties = sanitize_properties(properties)
                
                # Build dynamic property assignment
                # Build dynamic property assignment with escaped keys
                prop_string = ", ".join([f"n.`{k}` = ${k}" for k in properties.keys()])
                
                query = f"""
                MERGE (n:{label} {{_node_id: $_node_id}})
                SET {prop_string}
                """
                
                try:
                    session.run(query, **properties)
                    count += 1
                except Exception as e:
                    print(f"⚠️ Node load error: {e}")
            
            return count
    
    @with_retry()
    def load_relationships(self, relationships: List[Dict[str, Any]], trace_id: str) -> int:
        """Load relationships into the LPG database."""
        if not relationships:
            return 0
        
        with self.manager.driver.session(database=self.db_name) as session:
            count = 0
            for rel in relationships:
                source = rel.get("source", "")
                target = rel.get("target", "")
                rel_type = rel.get("type", "RELATED_TO")
                properties = rel.get("properties", {})
                
                # Sanitize relationship type
                rel_type = rel_type.replace(" ", "_").replace("-", "_").upper()
                
                # Add trace_id to properties
                properties["_trace_id"] = trace_id
                
                # Sanitize properties for Neo4j
                properties = sanitize_properties(properties)
                
                query = f"""
                MATCH (a {{_node_id: $source}})
                MATCH (b {{_node_id: $target}})
                MERGE (a)-[r:{rel_type}]->(b)
                SET r += $props
                """
                
                try:
                    session.run(query, source=source, target=target, props=properties)
                    count += 1
                except Exception as e:
                    print(f"⚠️ Relationship load error: {e}")
            
            return count
    
    @with_retry()
    def load_chunk(self, input_text: str, trace_id: str) -> bool:
        """Create a Chunk node from input_text for source traceability."""
        if not input_text:
            return False
        
        with self.manager.driver.session(database=self.db_name) as session:
            query = """
            MERGE (c:Chunk {_trace_id: $trace_id})
            SET c.text = $input_text,
                c.char_count = $char_count
            """
            
            try:
                session.run(query, 
                           trace_id=trace_id, 
                           input_text=input_text,
                           char_count=len(input_text))
                return True
            except Exception as e:
                print(f"⚠️ Chunk load error: {e}")
                return False
    
    @with_retry()
    def create_extracted_from_relationships(self, nodes: List[Dict[str, Any]], trace_id: str) -> int:
        """Create EXTRACTED_FROM relationships from Entity nodes to Chunk for data provenance."""
        if not nodes:
            return 0
        
        with self.manager.driver.session(database=self.db_name) as session:
            count = 0
            for node in nodes:
                node_id = node.get("id", "")
                
                query = """
                MATCH (e {_node_id: $node_id})
                MATCH (c:Chunk {_trace_id: $trace_id})
                MERGE (e)-[r:EXTRACTED_FROM]->(c)
                SET r._trace_id = $trace_id
                """
                
                try:
                    session.run(query, node_id=node_id, trace_id=trace_id)
                    count += 1
                except Exception as e:
                    print(f"⚠️ EXTRACTED_FROM relationship error: {e}")
            
            return count

    @with_retry()
    def create_fulltext_index(self):
        """Create fulltext index for keyword search on Entities and Chunks."""
        with self.manager.driver.session(database=self.db_name) as session:
            try:
                # Index commonly searched properties
                query = """
                CREATE FULLTEXT INDEX entity_fulltext IF NOT EXISTS
                FOR (n:Entity|Chunk)
                ON EACH [n.name, n.text, n.description, n.id, n.title]
                """
                session.run(query)
                print("✅ Created LPG fulltext index: entity_fulltext")
            except Exception as e:
                # Ignore if already exists
                if "already exists" in str(e):
                    print("ℹ️ LPG fulltext index already exists.")
                else:
                    print(f"⚠️ Failed to create LPG fulltext index: {e}")


class RDFLoader:
    """Loads RDF triples into Neo4j."""
    
    def __init__(self, manager: Neo4jGraphManager, db_name: str):
        self.manager = manager
        self.db_name = db_name
    
    @with_retry()
    def load_triples(self, triples: List[Dict[str, Any]], trace_id: str) -> int:
        """Load RDF triples into the database."""
        if not triples:
            return 0
        
        with self.manager.driver.session(database=self.db_name) as session:
            count = 0
            for triple in triples:
                subject = triple.get("subject", "")
                predicate = triple.get("predicate", "")
                obj = triple.get("object", "")
                is_literal = triple.get("is_literal", False)
                
                # Extract local name from URI for relationship type
                predicate_local = predicate.split(":")[-1] if ":" in predicate else predicate
                predicate_local = predicate_local.replace("-", "_").replace(" ", "_").upper()
                
                if is_literal:
                    # Store literal as property on resource node
                    # MERGE on uri only to avoid n10s constraint conflicts
                    query = f"""
                    MERGE (s:Resource {{uri: $subject}})
                    ON CREATE SET s._trace_id = $trace_id
                    SET s.`{predicate_local}` = $object
                    """
                    try:
                        session.run(query, subject=subject, object=obj, trace_id=trace_id)
                        count += 1
                    except Exception as e:
                        print(f"⚠️ Triple (literal) load error: {e}")
                else:
                    # Create relationship between resource nodes
                    # MERGE on uri only to avoid n10s constraint conflicts
                    query = f"""
                    MERGE (s:Resource {{uri: $subject}})
                    ON CREATE SET s._trace_id = $trace_id
                    MERGE (o:Resource {{uri: $object}})
                    ON CREATE SET o._trace_id = $trace_id
                    MERGE (s)-[r:{predicate_local}]->(o)
                    SET r._predicate = $predicate
                    """
                    try:
                        session.run(query, subject=subject, object=obj, predicate=predicate, trace_id=trace_id)
                        count += 1
                    except Exception as e:
                        print(f"⚠️ Triple (relationship) load error: {e}")
            
            return count

    @with_retry()
    def create_fulltext_index(self):
        """Create fulltext index for keyword search on Resources."""
        with self.manager.driver.session(database=self.db_name) as session:
            try:
                # Index URI and semantic properties
                query = """
                CREATE FULLTEXT INDEX resource_fulltext IF NOT EXISTS
                FOR (n:Resource)
                ON EACH [n.uri, n.label, n.comment, n.definition, n.skos__prefLabel]
                """
                session.run(query)
                print("✅ Created RDF fulltext index: resource_fulltext")
            except Exception as e:
                if "already exists" in str(e):
                    print("ℹ️ RDF fulltext index already exists.")
                else:
                    print(f"⚠️ Failed to create RDF fulltext index: {e}")


# ==========================================
# Main Indexing Function
# ==========================================
def load_traces_data(file_path: str) -> List[Dict[str, Any]]:
    """Load traces from JSON file."""
    print(f"📂 Loading data from {file_path}...")
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"✅ Loaded {len(data)} traces.")
    return data


def build_graph_index():
    """Main function to build LPG and RDF indexes."""
    print("=" * 60)
    print("🚀 GraphAgent Indexing - Starting")
    print("=" * 60)
    
    # Load data
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_path = os.path.join(script_dir, DATA_FILE)
    traces = load_traces_data(data_path)
    
    # Initialize Neo4j manager
    manager = Neo4jGraphManager(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
    
    try:
        manager.connect()
        
        # Create databases
        print("\n📦 Setting up databases...")
        manager.create_database(LPG_DATABASE)
        manager.create_database(RDF_DATABASE)
        
        # Clear existing data (optional - comment out to append)
        manager.clear_database(LPG_DATABASE)
        manager.clear_database(RDF_DATABASE)
        
        # Setup indexes and constraints
        manager.create_lpg_indexes(LPG_DATABASE)
        manager.setup_rdf_constraints(RDF_DATABASE)
        
        # Initialize loaders
        lpg_loader = LPGLoader(manager, LPG_DATABASE)
        rdf_loader = RDFLoader(manager, RDF_DATABASE)
        
        # Statistics
        total_nodes = 0
        total_relationships = 0
        total_triples = 0
        total_chunks = 0
        total_extracted_from = 0
        
        # Process each trace
        print("\n⚙️ Processing traces...")
        for trace in tqdm(traces, desc="Indexing Traces"):
            trace_id = trace.get("trace_id", "unknown")
            input_text = trace.get("input_text", "")
            
            # Parse JSON strings from the exported format
            try:
                nodes = json.loads(trace.get("lpg_nodes", "[]"))
                relationships = json.loads(trace.get("lpg_edges", "[]"))
                rdf_triples = json.loads(trace.get("rdf_triples", "[]"))
            except json.JSONDecodeError as e:
                print(f"⚠️ JSON parse error for trace {trace_id}: {e}")
                continue
            
            # Load LPG data
            total_nodes += lpg_loader.load_nodes(nodes, trace_id)
            total_relationships += lpg_loader.load_relationships(relationships, trace_id)
            
            # Create Chunk node and EXTRACTED_FROM relationships
            if lpg_loader.load_chunk(input_text, trace_id):
                total_chunks += 1
                total_extracted_from += lpg_loader.create_extracted_from_relationships(nodes, trace_id)
            
            # Load RDF data
            total_triples += rdf_loader.load_triples(rdf_triples, trace_id)

        # Create Fulltext Indexes
        print("\n🔍 Creating fulltext indexes...")
        lpg_loader.create_fulltext_index()
        rdf_loader.create_fulltext_index()
        
        # Summary
        print("\n" + "=" * 60)
        print("✅ Indexing Complete!")
        print("=" * 60)
        print(f"📊 Summary:")
        print(f"   - LPG Nodes: {total_nodes}")
        print(f"   - LPG Chunks: {total_chunks}")
        print(f"   - LPG Relationships: {total_relationships}")
        print(f"   - LPG EXTRACTED_FROM: {total_extracted_from}")
        print(f"   - RDF Triples: {total_triples}")
        print(f"   - Total Traces Processed: {len(traces)}")
        print("=" * 60)
        
    except Exception as e:
        print(f"❌ Error during indexing: {e}")
        raise
    finally:
        manager.close()


if __name__ == "__main__":
    build_graph_index()
