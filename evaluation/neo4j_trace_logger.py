
import os
import uuid
import uuid
from datetime import datetime
from neo4j import GraphDatabase

class Neo4jTraceLogger:
    def __init__(self, database="agent_traces"):
        uri = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "password")
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.database = database
        self.session_id = str(uuid.uuid4())
        self.last_step_id = None # To support Linked List (:Step)-[:NEXT]->(:Step)
        
        # Ensure session node exists
        self._log_session()

    def close(self):
        self.driver.close()

    def _log_session(self):
        query = """
        MERGE (s:Session {id: $id})
        ON CREATE SET s.start_time = datetime()
        """
        try:
            with self.driver.session(database=self.database) as session:
                session.run(query, id=self.session_id)
        except Exception as e:
            print(f"❌ Failed to log session to '{self.database}': {e}")

    def log_trace(self, trace_name, query_text):
        trace_id = str(uuid.uuid4())
        self.last_step_id = None # Reset for new trace
        
        cypher = """
        MATCH (s:Session {id: $session_id})
        CREATE (t:Trace {id: $trace_id, name: $name, query: $query, timestamp: datetime()})
        CREATE (s)-[:HAS_TRACE]->(t)
        RETURN t.id
        """
        with self.driver.session(database=self.database) as session:
            session.run(cypher, session_id=self.session_id, trace_id=trace_id, name=trace_name, query=query_text)
        return trace_id

    def log_step(self, trace_id, agent_name, step_type, content, metadata=None):
        step_id = str(uuid.uuid4())
        if metadata is None: metadata = {}
        
        # Base query to create the node
        cypher = """
        MATCH (t:Trace {id: $trace_id})
        MERGE (a:Agent {name: $agent})
        CREATE (st:Step {id: $step_id, type: $type, agent: $agent, content: $content, timestamp: datetime()})
        SET st += $metadata
        
        CREATE (t)-[:HAS_STEP]->(st)
        CREATE (st)-[:USED_AGENT]->(a)
        """
        
        # Link to previous step (Linked List)
        if self.last_step_id:
            cypher += """
            WITH st
            MATCH (prev:Step {id: $prev_id})
            CREATE (prev)-[:NEXT]->(st)
            """
        
        with self.driver.session(database=self.database) as session:
            session.run(cypher, trace_id=trace_id, step_id=step_id, prev_id=self.last_step_id,
                       type=step_type, agent=agent_name, content=content, metadata=metadata)
        
        # Update pointer
        self.last_step_id = step_id
        return step_id

    def log_handoff(self, trace_id, from_agent, to_agent, reason="Handoff"):
        """
        Convenience method to log a handoff step.
        """
        content = f"Handing off control from {from_agent} to {to_agent}"
        return self.log_step(trace_id, from_agent, "HANDOFF", content, 
                            metadata={"to_agent": to_agent, "reason": reason})

