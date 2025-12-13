
import os
import socket
from neo4j import GraphDatabase

def run_diagnostics():
    # 1. Check if we are in Docker
    in_docker = os.path.exists('/.dockerenv')
    print(f"🐳 Running inside Docker? {'YES' if in_docker else 'NO'}")

    # 2. Hostname Resolution Test
    print("\n--- DNS Resolution ---")
    # 'neo4j' is the service name in docker-compose. 'graphrag-neo4j' is container name (sometimes accessible).
    # 'localhost' for local dev.
    hosts_to_check = ['neo4j', 'graphrag-neo4j', 'localhost']
    for host in hosts_to_check:
        try:
            ip = socket.gethostbyname(host)
            print(f"✅ {host} -> {ip}")
        except Exception as e:
            print(f"❌ {host} -> FAILED ({e})")

    # 3. Connection Test
    print("\n--- Neo4j Connection ---")
    if in_docker:
        # In Docker, we MUST use the service name 'neo4j' or 'graphrag-neo4j'
        target = 'neo4j'
    else:
        # Local, we MUST use localhost
        target = 'localhost'

    # Allow override via env var
    target = os.getenv('NEO4J_HOST', target)
    uri = f"bolt://{target}:7687"
    print(f"Attempting connection to {uri}...")

    try:
        user = os.getenv('NEO4J_USER', 'neo4j')
        password = os.getenv('NEO4J_PASSWORD', 'password')
        auth = (user, password)
        
        driver = GraphDatabase.driver(uri, auth=auth)
        driver.verify_connectivity()
        print("🎉 SUCCESS! Connected.")
        driver.close()
    except Exception as e:
        print(f"❌ CONNECTION FAILED: {e}")
        
        print("\n--- TROUBLESHOOTING GUIDE ---")
        if in_docker:
            print("1. If 'neo4j' DNS failed: The containers might not be on the same network named 'graphrag-net'.")
            print("2. If DNS works but Connection Refused: Neo4j container is running but not listening (starting up or crashed).")
        else:
            print("1. You are running LOCALLY. Ensure 'docker-compose up' is running successfully in another terminal.")
            print("2. Check if Neo4j is actually running: 'docker ps' | grep neo4j'")
            print("3. Check Neo4j logs: 'docker logs graphrag-neo4j' (Look for 'Started' message)")
            print("4. If logs say 'Plugin failure', the open-gds.jar might be missing/corrupt in data/neo4j/plugins.")

if __name__ == "__main__":
    run_diagnostics()
