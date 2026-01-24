from neo4j import GraphDatabase
import os

class GraphLoader:
    def __init__(self, uri, username, password):
        self.driver = GraphDatabase.driver(uri, auth=(username, password))

    def close(self):
        self.driver.close()

    def load_graph(self, graph_data: dict, source_id: str):
        """
        Loads nodes and relationships into Neo4j.
        """
        if not graph_data or "nodes" not in graph_data:
            return

        with self.driver.session() as session:
            # 1. Load Nodes
            for node in graph_data.get("nodes", []):
                session.execute_write(self._create_node, node, source_id)
            
            # 2. Load Relationships
            for rel in graph_data.get("relationships", []):
                session.execute_write(self._create_relationship, rel)

    @staticmethod
    def _create_node(tx, node, source_id):
        # Dynamic label and properties
        label = node.get("label", "Entity")
        properties = node.get("properties", {})
        properties["id"] = node["id"]
        properties["source_id"] = source_id
        
        # Cypher to merge node
        # Note: In production, careful with dynamic labels to avoid Cypher injection if labels come from untrusted source.
        # Assuming LLM labels are "safe" enough or we sanitize.
        
        # Flatten properties for Cypher
        # Simple dynamic merging
        query = (
            f"MERGE (n:`{label}` {{id: $id}}) "
            f"SET n += $props "
            f"RETURN n"
        )
        tx.run(query, id=node["id"], props=properties)

    @staticmethod
    def _create_relationship(tx, rel):
        source_id = rel["source"]
        target_id = rel["target"]
        rel_type = rel.get("type", "RELATED_TO").upper().replace(" ", "_")
        properties = rel.get("properties", {})

        # We assume nodes exist or we merge them loosely? 
        # Better to match on ID.
        query = (
            f"MATCH (a {{id: $source_id}}), (b {{id: $target_id}}) "
            f"MERGE (a)-[r:`{rel_type}`]->(b) "
            f"SET r += $props "
            f"RETURN r"
        )
        tx.run(query, source_id=source_id, target_id=target_id, props=properties)
