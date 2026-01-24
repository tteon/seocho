import os
from neo4j import GraphDatabase

# --- Configuration ---
URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
USER = os.getenv("NEO4J_USER", "neo4j")
PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

def generate_mock_data():
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    
    with driver.session() as session:
        # 1. Cleanup
        print("Cleaning up old mock data...")
        session.run("MATCH (n:MockData) DETACH DELETE n")

        # 2. Define FIBO-style Categories
        # fibo-fnd-gao-obj:BusinessFunction -> Domain
        # fibo-fnd-arr-prod:Product -> Data Product
        # fibo-fnd-dt-fd:Data -> Dataset

        cypher = """
        // Create Domains (Business Functions)
        CREATE (treasury:MockData:`fibo-fnd-gao-obj:BusinessFunction` {name: 'Treasury', description: 'Manages liquidity and capital'})
        CREATE (risk:MockData:`fibo-fnd-gao-obj:BusinessFunction` {name: 'Risk Management', description: 'Identifies and mitigates risks'})
        CREATE (compliance:MockData:`fibo-fnd-gao-obj:BusinessFunction` {name: 'Regulatory Compliance', description: 'Ensures adherence to laws'})

        // Create Data Products
        CREATE (prod_liquidity:MockData:`fibo-fnd-arr-prod:Product` {name: 'Liquidity Dashboard', lifecycleStatus: 'Production'})
        CREATE (prod_credit:MockData:`fibo-fnd-arr-prod:Product` {name: 'Credit Risk Model', lifecycleStatus: 'Beta'})
        CREATE (prod_ledger:MockData:`fibo-fnd-arr-prod:Product` {name: 'Transaction Ledger', lifecycleStatus: 'Production'})

        // Create Datasets (Data Assets)
        CREATE (data_cash:MockData:`fibo-fnd-dt-fd:Data` {name: 'Daily Cash Position', format: 'Parquet'})
        CREATE (data_limit:MockData:`fibo-fnd-dt-fd:Data` {name: 'Counterparty Limits', format: 'CSV'})
        CREATE (data_trades:MockData:`fibo-fnd-dt-fd:Data` {name: 'FX Trades', format: 'Avro'})

        // --- Relationships ---
        
        // Products governed by Domains
        CREATE (prod_liquidity)-[:GOVERNED_BY]->(treasury)
        CREATE (prod_credit)-[:GOVERNED_BY]->(risk)
        CREATE (prod_ledger)-[:GOVERNED_BY]->(compliance)

        // Products produce Data
        CREATE (prod_liquidity)-[:PRODUCES]->(data_cash)
        CREATE (prod_credit)-[:PRODUCES]->(data_limit)
        CREATE (prod_ledger)-[:PRODUCES]->(data_trades)

        // Data dependencies (Lineage)
        // e.g. Credit Model consumes FX Trades
        CREATE (data_trades)-[:IS_INPUT_TO]->(prod_credit)
        
        """
        
        print("Executing Cypher to create FIBO Data Mesh...")
        session.run(cypher)
        print("Mock Data Mesh created successfully.")

    driver.close()

if __name__ == "__main__":
    generate_mock_data()
