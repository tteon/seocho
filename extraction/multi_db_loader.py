
import os
import ast
import pandas as pd
from neo4j import GraphDatabase

# Configuration per user snippet
DB_MAPPING = {
    "BASELINE": "kgnormal",
    "FIBO": "kgfibo"
}

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

def parse_record(record_str):
    try:
        if isinstance(record_str, str):
            # Safe eval for python literals
            return ast.literal_eval(record_str)
        return record_str
    except (ValueError, SyntaxError):
        return {}

def ingest_to_specific_db(driver, df, mode, target_db):
    print(f"\n🚀 [Mode: {mode}] -> [DB: {target_db}] 적재 시작 (총 {len(df)}건)")
    
    # 쿼리: 노드 생성 (Query: Create Nodes)
    node_query = """
    UNWIND $batch AS row
    MERGE (e:Entity {name: row.text})
    ON CREATE SET e.type = row.type, e.source_mode = $mode
    """
    
    try:
        # Use session with specific database
        with driver.session(database=target_db) as session:
            
            # --- [Step 1] Load Nodes ---
            batch_nodes = []
            for _, row in df.iterrows():
                # parsing logic assumes input df has 'extracted_entities' column
                # which is a string repr of dict: {'extracted_entities': [...]}
                parsed_ent = parse_record(row.get('extracted_entities', '{}'))
                entities = parsed_ent.get('extracted_entities', [])
                
                # Adapting to potentially different structure if needed, 
                # but following user snippet strictly for now.
                # User's snippet assumes entities have 'text' and 'type' keys presumably.
                # Let's verify structure in a real run, but here we implement the logic.
                batch_nodes.extend(entities)
                
                if len(batch_nodes) >= 1000:
                    session.run(node_query, batch=batch_nodes, mode=mode)
                    batch_nodes = []
            
            if batch_nodes:
                session.run(node_query, batch=batch_nodes, mode=mode)
            print(f"   ✅ 노드 생성 완료 (Nodes Created @{target_db})")

            # --- [Step 2] Load Relationships ---
            rels_by_type = {}
            for _, row in df.iterrows():
                parsed_rel = parse_record(row.get('linked_relationships', '{}'))
                relationships = parsed_rel.get('entity_relationships', [])
                
                for rel in relationships:
                    r_type = rel.get('relation_type', 'RELATED').strip().upper().replace(" ", "_")
                    if not r_type: continue
                    
                    if r_type not in rels_by_type:
                        rels_by_type[r_type] = []
                    
                    rels_by_type[r_type].append({
                        "source": rel.get('source_entity'),
                        "target": rel.get('target_entity')
                    })

            count_rels = 0
            for r_type, batch_data in rels_by_type.items():
                rel_query = f"""
                UNWIND $batch AS row
                MATCH (source:Entity {{name: row.source}})
                MATCH (target:Entity {{name: row.target}})
                MERGE (source)-[:{r_type}]->(target)
                """
                
                batch_size = 1000
                for i in range(0, len(batch_data), batch_size):
                    chunk = batch_data[i:i + batch_size]
                    session.run(rel_query, batch=chunk)
                    count_rels += len(chunk)
                    
            print(f"   🔗 관계 연결 완료 (Rels Linked @{target_db}, {count_rels}건)")
            
    except Exception as e:
        print(f"❌ [Error] '{target_db}' 데이터베이스 접속 실패: {e}")
        print("   (Tip: Enterprise 버전이 아니거나, DB가 생성되지 않았을 수 있습니다.)")

def run_multidb_ingest(full_df):
    # Driver connected once
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    try:
        driver.verify_connectivity()
    except Exception as e:
        print(f"❌ Neo4j 서버 연결 실패: {e}")
        return

    # Check for 'experiment_mode' column
    if 'experiment_mode' not in full_df.columns:
        print("⚠️ 'experiment_mode' column missing. Defaulting to 'BASELINE'.")
        full_df['experiment_mode'] = 'BASELINE'

    modes = full_df['experiment_mode'].unique()
    print(f"🔎 감지된 실험 모드 (Detected Modes): {modes}")
    
    for mode in modes:
        if mode not in DB_MAPPING:
            print(f"⚠️ 경고: 모드 '{mode}'에 매핑된 DB가 없습니다. 건너뜁니다.")
            continue
            
        target_db_name = DB_MAPPING[mode]
        subset_df = full_df[full_df['experiment_mode'] == mode]
        
        # Reuse driver
        ingest_to_specific_db(driver, subset_df, mode, target_db_name)

    driver.close()
    print("\n🎉 모든 데이터 적재 완료 (All Data Loaded)!")

if __name__ == "__main__":
    # Example mock run
    print("Running Multi-DB Ingest Demo...")
    # Mock DataFrame matching user structure expectation
    # 'extracted_entities': stringified dict with list of entities
    # 'linked_relationships': stringified dict with list of rels
    
    mock_data = [
        {
            "experiment_mode": "BASELINE",
            "extracted_entities": str({"extracted_entities": [{"text": "Apple", "type": "ORG"}, {"text": "iPhone", "type": "PRODUCT"}]}),
            "linked_relationships": str({"entity_relationships": [{"source_entity": "Apple", "target_entity": "iPhone", "relation_type": "MAKES"}]})
        },
        {
            "experiment_mode": "FIBO",
            "extracted_entities": str({"extracted_entities": [{"text": "Interest Rate", "type": "INDICATOR"}, {"text": "Fed", "type": "ORG"}]}),
            "linked_relationships": str({"entity_relationships": [{"source_entity": "Fed", "target_entity": "Interest Rate", "relation_type": "SETS"}]})
        }
    ]
    df = pd.DataFrame(mock_data)
    
    # Ensure DBs exist first (calling the other script logic ideally, or assuming user ran manage_databases.py)
    # run_multidb_ingest(df)
    print("Call run_multidb_ingest(df) to execute.")
