from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
from agent import GraphAgent
from neo4j_client import Neo4jClient
from cdc import DataHubCDC

app = FastAPI()
agent = GraphAgent()
neo4j_client = Neo4jClient()
cdc = DataHubCDC()

@app.on_event("startup")
async def startup_event():
    # Initialize n10s
    neo4j_client.init_n10s()
    # Start CDC Consumer
    cdc.start()

class Document(BaseModel):
    id: str
    content: str
    source: str

@app.post("/ingest")
async def ingest_documents(documents: List[Document]):
    print(f"Received {len(documents)} documents for ingestion.")
    try:
        for doc in documents:
            # 1. Store Document Node in Graph
            neo4j_client.create_document_node(doc.id, doc.content, doc.source)
            
            # 2. Agent extracts entities
            entities = agent.process_document(doc.content)
            
            # 3. Store Entities and Relationships
            for entity in entities:
                neo4j_client.create_relationship(doc.id, entity["name"], entity["type"])
                
        return {"status": "success", "processed": len(documents)}
    except Exception as e:
        print(f"Error processing documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.on_event("shutdown")
def shutdown_event():
    cdc.stop()
    neo4j_client.close()
