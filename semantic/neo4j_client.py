import os
from neo4j import GraphDatabase

class Neo4jClient:
    def __init__(self):
        uri = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "password")
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self):
        self.driver.close()

    def create_document_node(self, doc_id, content, source):
        with self.driver.session() as session:
            session.execute_write(self._create_and_return_node, doc_id, content, source)

    @staticmethod
    def _create_and_return_node(tx, doc_id, content, source):
        query = (
            "MERGE (d:Document {id: $doc_id}) "
            "SET d.content = $content, d.source = $source "
            "RETURN d"
        )
        result = tx.run(query, doc_id=doc_id, content=content, source=source)
        return result.single()[0]

    def create_relationship(self, doc_id, entity_name, relationship_type):
        with self.driver.session() as session:
            session.execute_write(self._create_entity_and_rel, doc_id, entity_name, relationship_type)

    @staticmethod
    def _create_entity_and_rel(tx, doc_id, entity_name, relationship_type):
        query = (
            "MATCH (d:Document {id: $doc_id}) "
            "MERGE (e:Entity {name: $entity_name}) "
            "MERGE (d)-[r:MENTIONS {type: $relationship_type}]->(e) "
            "RETURN r"
        )
        tx.run(query, doc_id=doc_id, entity_name=entity_name, relationship_type=relationship_type)

    def init_n10s(self):
        # Schema operations in Neo4j often need to be in their own implicit transactions (auto-commit),
        # especially when mixing them or if they are procedure calls that do schema mods.
        # We use a session and run them directly.
        with self.driver.session() as session:
            # 1. Init n10s
            try:
                session.run("CALL n10s.graphconfig.init()")
            except Exception as e:
                print(f"n10s init warning (might be already initialized): {e}")

            # 2. Create constraint
            try:
                session.run("CREATE CONSTRAINT n10s_unique_uri IF NOT EXISTS FOR (r:Resource) REQUIRE r.uri IS UNIQUE")
            except Exception as e:
                print(f"n10s constraint warning: {e}")

    def merge_datahub_entity(self, urn):
        with self.driver.session() as session:
            session.execute_write(self._merge_datahub_entity_tx, urn)

    @staticmethod
    def _merge_datahub_entity_tx(tx, urn):
        query = (
            "MERGE (e:DataHubEntity {urn: $urn}) "
            "SET e.last_updated = timestamp() "
            "RETURN e"
        )
        tx.run(query, urn=urn)
