import os
import pandas as pd
import matplotlib.pyplot as plt
from neo4j import GraphDatabase
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://graphrag-neo4j:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

def analyze_db(driver, db_name):
    print(f"\n--- Analyzing Database: {db_name} ---")
    with driver.session(database=db_name) as session:
        # Node count
        node_count = session.run("MATCH (n) RETURN count(n) as count").single()["count"]
        print(f"Total Nodes: {node_count}")
        
        # Relationship count
        rel_count = session.run("MATCH ()-[r]->() RETURN count(r) as count").single()["count"]
        print(f"Total Relationships: {rel_count}")
        
        if node_count == 0:
            print("Database is empty.")
            return
        
        # Degree distribution
        query = """
        MATCH (n)
        RETURN count{(n)--()} as degree
        """
        result = session.run(query)
        degrees = [record["degree"] for record in result]
        df = pd.DataFrame(degrees, columns=["degree"])
        
        # Summary stats
        print("Degree Statistics:")
        print(df.describe())
        
        # Power law check (simple log-log plot)
        degree_counts = df["degree"].value_counts().sort_index()
        
        plt.figure(figsize=(10, 6))
        plt.loglog(degree_counts.index, degree_counts.values, 'bo')
        plt.title(f"Degree Distribution (Log-Log) - {db_name}")
        plt.xlabel("Degree (k)")
        plt.ylabel("Frequency P(k)")
        plt.grid(True, which="both", ls="-", alpha=0.5)
        
        plot_path = f"/workspace/output/degree_dist_{db_name}.png"
        plt.savefig(plot_path)
        print(f"Plot saved to {plot_path}")
        
        # Check for "Super-nodes" (high degree)
        high_degree_query = """
        MATCH (n)
        WITH n, count{(n)--()} as degree
        ORDER BY degree DESC
        LIMIT 5
        RETURN labels(n) as labels, n.id as id, n.name as name, n.uri as uri, degree
        """
        print("\nTop 5 High-Degree Nodes:")
        high_nodes = session.run(high_degree_query)
        for node in high_nodes:
            name = node["name"] or node["uri"] or node["id"]
            print(f"Labels: {node['labels']}, Name/ID: {name}, Degree: {node['degree']}")

def main():
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        analyze_db(driver, "lpg")
        analyze_db(driver, "rdf")
    finally:
        driver.close()

if __name__ == "__main__":
    # Ensure output directory exists
    os.makedirs("/workspace/output", exist_ok=True)
    main()
