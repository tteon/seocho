"""
Neo4j Graph Indexer
Loads LPG and RDF data into Neo4j databases.
Refactored from graphagent_indexing.py
"""
import os
import json
import time
from typing import List, Dict, Any, Optional
from functools import wraps
from neo4j import GraphDatabase, Driver
from neo4j.exceptions import ServiceUnavailable, TransientError, ClientError
from tqdm import tqdm

from src.config.settings import (
    NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
    LPG_DATABASE, RDF_DATABASE,
    MAX_RETRIES, RETRY_DELAY, BATCH_SIZE,
    KGBUILD_TRACES_PATH
)


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


def sanitize_properties(properties: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize property keys and values for Neo4j compatibility."""
    sanitized = {}
    for key, value in properties.items():
        clean_key = key.replace("&", "and").replace("-", "_").replace(" ", "_").replace("(", "").replace(")", "")
        if isinstance(value, (dict, list)):
            sanitized[clean_key] = json.dumps(value)
        else:
            sanitized[clean_key] = value
    return sanitized


class Neo4jIndexer:
    """
    Indexes data into Neo4j LPG and RDF databases.
    """
    
    def __init__(self, uri: str = None, user: str = None, password: str = None):
        self.uri = uri or NEO4J_URI
        self.user = user or NEO4J_USER
        self.password = password or NEO4J_PASSWORD
        self.driver: Optional[Driver] = None
    
    def connect(self):
        """Establish connection to Neo4j."""
        print(f"🔗 Connecting to Neo4j at {self.uri}...")
        self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
        self.driver.verify_connectivity()
        print("✅ Connected to Neo4j successfully.")
        return self
    
    def close(self):
        """Close the Neo4j connection."""
        if self.driver:
            self.driver.close()
            print("🔌 Neo4j connection closed.")
    
    @with_retry()
    def create_database(self, db_name: str):
        """Create a database if it doesn't exist."""
        with self.driver.session(database="system") as session:
            result = session.run("SHOW DATABASES")
            existing_dbs = [record["name"] for record in result]
            
            if db_name in existing_dbs:
                print(f"📦 Database '{db_name}' already exists.")
            else:
                session.run(f"CREATE DATABASE {db_name} IF NOT EXISTS")
                print(f"✅ Database '{db_name}' created.")
                time.sleep(2)
    
    @with_retry()
    def clear_database(self, db_name: str):
        """Clear all data from a database."""
        with self.driver.session(database=db_name) as session:
            session.run("MATCH (n) DETACH DELETE n")
            print(f"🧹 Cleared all data from '{db_name}'.")
    
    @with_retry()
    def create_lpg_indexes(self):
        """Create indexes for LPG database."""
        with self.driver.session(database=LPG_DATABASE) as session:
            try:
                session.run("CREATE INDEX node_id_index IF NOT EXISTS FOR (n:Node) ON (n.id)")
                print(f"✅ Indexes created for '{LPG_DATABASE}'.")
            except ClientError as e:
                print(f"⚠️ Index creation note: {e}")
    
    @with_retry()
    def create_fulltext_indexes(self):
        """Create fulltext indexes for LPG and RDF."""
        # LPG fulltext
        with self.driver.session(database=LPG_DATABASE) as session:
            try:
                session.run("""
                    CREATE FULLTEXT INDEX entity_fulltext IF NOT EXISTS
                    FOR (n:Entity|Chunk)
                    ON EACH [n.name, n.text, n.description, n.id, n.title]
                """)
                print("✅ Created LPG fulltext index")
            except Exception as e:
                if "already exists" in str(e):
                    print("ℹ️ LPG fulltext index already exists.")
                else:
                    print(f"⚠️ LPG fulltext index: {e}")
        
        # RDF fulltext
        with self.driver.session(database=RDF_DATABASE) as session:
            try:
                session.run("""
                    CREATE FULLTEXT INDEX resource_fulltext IF NOT EXISTS
                    FOR (n:Resource)
                    ON EACH [n.uri, n.label, n.comment, n.definition, n.skos__prefLabel]
                """)
                print("✅ Created RDF fulltext index")
            except Exception as e:
                if "already exists" in str(e):
                    print("ℹ️ RDF fulltext index already exists.")
                else:
                    print(f"⚠️ RDF fulltext index: {e}")
    
    def load_nodes(self, nodes: List[Dict], trace_id: str) -> int:
        """Load nodes into the LPG database."""
        if not nodes:
            return 0
        
        with self.driver.session(database=LPG_DATABASE) as session:
            count = 0
            for node in nodes:
                node_id = node.get("id", "")
                label = node.get("label", "Node").replace(" ", "_").replace("-", "_")
                properties = node.get("properties", {})
                properties["_trace_id"] = trace_id
                properties["_node_id"] = node_id
                properties = sanitize_properties(properties)
                
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
    
    def load_relationships(self, relationships: List[Dict], trace_id: str) -> int:
        """Load relationships into the LPG database."""
        if not relationships:
            return 0
        
        with self.driver.session(database=LPG_DATABASE) as session:
            count = 0
            for rel in relationships:
                source = rel.get("source", "")
                target = rel.get("target", "")
                rel_type = rel.get("type", "RELATED_TO").replace(" ", "_").replace("-", "_").upper()
                properties = sanitize_properties(rel.get("properties", {}))
                properties["_trace_id"] = trace_id
                
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
    
    def load_chunk(self, input_text: str, trace_id: str) -> bool:
        """Create a Chunk node from input text."""
        if not input_text:
            return False
        
        with self.driver.session(database=LPG_DATABASE) as session:
            try:
                session.run("""
                    MERGE (c:Chunk {_trace_id: $trace_id})
                    SET c.text = $input_text, c.char_count = $char_count
                """, trace_id=trace_id, input_text=input_text, char_count=len(input_text))
                return True
            except Exception as e:
                print(f"⚠️ Chunk load error: {e}")
                return False
    
    def create_extracted_from_relationships(self, nodes: List[Dict], trace_id: str) -> int:
        """Create EXTRACTED_FROM relationships from entities to chunks."""
        if not nodes:
            return 0
        
        with self.driver.session(database=LPG_DATABASE) as session:
            count = 0
            for node in nodes:
                node_id = node.get("id", "")
                try:
                    session.run("""
                        MATCH (e {_node_id: $node_id})
                        MATCH (c:Chunk {_trace_id: $trace_id})
                        MERGE (e)-[r:EXTRACTED_FROM]->(c)
                        SET r._trace_id = $trace_id
                    """, node_id=node_id, trace_id=trace_id)
                    count += 1
                except Exception as e:
                    print(f"⚠️ EXTRACTED_FROM error: {e}")
            return count
    
    def load_rdf_triples(self, triples: List[Dict], trace_id: str) -> int:
        """Load RDF triples into the RDF database."""
        if not triples:
            return 0
        
        with self.driver.session(database=RDF_DATABASE) as session:
            count = 0
            for triple in triples:
                subject = triple.get("subject", "")
                predicate = triple.get("predicate", "")
                obj = triple.get("object", "")
                is_literal = triple.get("is_literal", False)
                
                predicate_local = predicate.split(":")[-1] if ":" in predicate else predicate
                predicate_local = predicate_local.replace("-", "_").replace(" ", "_").upper()
                
                try:
                    if is_literal:
                        session.run(f"""
                            MERGE (s:Resource {{uri: $subject}})
                            ON CREATE SET s._trace_id = $trace_id
                            SET s.`{predicate_local}` = $object
                        """, subject=subject, object=obj, trace_id=trace_id)
                    else:
                        session.run(f"""
                            MERGE (s:Resource {{uri: $subject}})
                            ON CREATE SET s._trace_id = $trace_id
                            MERGE (o:Resource {{uri: $object}})
                            ON CREATE SET o._trace_id = $trace_id
                            MERGE (s)-[r:{predicate_local}]->(o)
                            SET r._predicate = $predicate
                        """, subject=subject, object=obj, predicate=predicate, trace_id=trace_id)
                    count += 1
                except Exception as e:
                    print(f"⚠️ Triple load error: {e}")
            return count
    
    def build_from_traces(self, file_path: str = None):
        """
        Build LPG and RDF indexes from exported traces.
        
        Args:
            file_path: Path to JSON file with trace data
        """
        file_path = file_path or KGBUILD_TRACES_PATH
        
        print("=" * 60)
        print("🚀 Neo4j Graph Indexing - Starting")
        print("=" * 60)
        
        # Load data
        print(f"📂 Loading data from {file_path}...")
        with open(file_path, "r", encoding="utf-8") as f:
            traces = json.load(f)
        print(f"✅ Loaded {len(traces)} traces.")
        
        # Setup databases
        self.create_database(LPG_DATABASE)
        self.create_database(RDF_DATABASE)
        self.clear_database(LPG_DATABASE)
        self.clear_database(RDF_DATABASE)
        self.create_lpg_indexes()
        
        # Statistics
        stats = {"nodes": 0, "relationships": 0, "triples": 0, "chunks": 0, "extracted_from": 0}
        
        # Process traces
        for trace in tqdm(traces, desc="Indexing Traces"):
            trace_id = trace.get("trace_id", trace.get("id", "unknown"))
            input_text = trace.get("input_text", trace.get("input", {}).get("input_text", ""))
            
            # Parse JSON strings
            try:
                nodes = json.loads(trace.get("lpg_nodes", "[]")) if isinstance(trace.get("lpg_nodes"), str) else trace.get("lpg_graph", {}).get("nodes", [])
                relationships = json.loads(trace.get("lpg_edges", "[]")) if isinstance(trace.get("lpg_edges"), str) else trace.get("lpg_graph", {}).get("relationships", [])
                rdf_triples = json.loads(trace.get("rdf_triples", "[]")) if isinstance(trace.get("rdf_triples"), str) else trace.get("rdf_triples", [])
            except json.JSONDecodeError as e:
                print(f"⚠️ JSON parse error for trace {trace_id}: {e}")
                continue
            
            # Load LPG
            stats["nodes"] += self.load_nodes(nodes, trace_id)
            stats["relationships"] += self.load_relationships(relationships, trace_id)
            
            # Create chunk and EXTRACTED_FROM
            if self.load_chunk(input_text, trace_id):
                stats["chunks"] += 1
                stats["extracted_from"] += self.create_extracted_from_relationships(nodes, trace_id)
            
            # Load RDF
            stats["triples"] += self.load_rdf_triples(rdf_triples, trace_id)
        
        # Create fulltext indexes
        self.create_fulltext_indexes()
        
        # Summary
        print("\n" + "=" * 60)
        print("✅ Indexing Complete!")
        print("=" * 60)
        print(f"📊 Summary:")
        print(f"   - LPG Nodes: {stats['nodes']}")
        print(f"   - LPG Chunks: {stats['chunks']}")
        print(f"   - LPG Relationships: {stats['relationships']}")
        print(f"   - LPG EXTRACTED_FROM: {stats['extracted_from']}")
        print(f"   - RDF Triples: {stats['triples']}")
        print(f"   - Total Traces: {len(traces)}")
        print("=" * 60)


def build_neo4j_index(file_path: str = None):
    """CLI entry point for Neo4j indexing."""
    indexer = Neo4jIndexer()
    try:
        indexer.connect()
        indexer.build_from_traces(file_path)
    finally:
        indexer.close()


if __name__ == "__main__":
    build_neo4j_index()
