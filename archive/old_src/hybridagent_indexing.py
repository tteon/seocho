import os
import lancedb
import pyarrow as pa
from openai import OpenAI
from opik import Opik
from tqdm import tqdm
from dotenv import load_dotenv

# 1. 환경 설정
load_dotenv()
DB_PATH = os.getenv("LANCEDB_PATH", "/workspace/data/lancedb")
TABLE_NAME = "fibo_context"
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536

OPENAI_CLIENT = OpenAI()
OPIK_CLIENT = Opik()

def get_embedding(text: str) -> list:
    text = text.replace("\n", " ")
    return OPENAI_CLIENT.embeddings.create(input=[text], model=EMBEDDING_MODEL).data[0].embedding

def build_hybrid_index():
    os.makedirs(DB_PATH, exist_ok=True)
    db = lancedb.connect(DB_PATH)

    print("🔍 Fetching dataset from Opik...")
    try:
        dataset = OPIK_CLIENT.get_dataset(name="fibo-evaluation-dataset")
        items = dataset.get_items()
    except Exception as e:
        print(f"Dataset Load Error: {e}")
        return

    print(f"✅ Found {len(items)} items. Starting ingestion...")

    data_to_insert = []
    seen_ids = set()

    for item in tqdm(items, desc="Processing Data"):
        metadata = item.get("metadata", {})
        refs = metadata.get("references", "")
        if not refs: continue
        
        # ID 설정 (Metadata ID 우선)
        custom_id = str(metadata.get("id")) if metadata.get("id") else item.get("id")
        if custom_id in seen_ids: continue
        seen_ids.add(custom_id)

        text_content = " ".join([str(r) for r in refs]) if isinstance(refs, list) else str(refs)
        vector = get_embedding(text_content)
        
        data_to_insert.append({
            "id": custom_id,
            "vector": vector,
            "text": text_content,
            "source": "opik_dataset"
        })

    # [수정 중요] LanceDB는 벡터 인덱싱을 위해 'fixed_size_list'를 권장합니다.
    schema = pa.schema([
        pa.field("id", pa.string()),
        pa.field("vector", pa.fixed_size_list(pa.float32(), EMBEDDING_DIM)), # 여기 수정됨
        pa.field("text", pa.string()),
        pa.field("source", pa.string())
    ])
    
    # 테이블 생성
    tbl = db.create_table(TABLE_NAME, data=data_to_insert, schema=schema, mode="overwrite")
    print(f"✅ Table created with {len(data_to_insert)} rows.")
    
    # ---------------------------------------------------------
    # Indexing (IVF-PQ + FTS)
    # ---------------------------------------------------------
    if len(data_to_insert) > 100:
        print("⚙️ Building Vector Index (IVF-PQ)...")
        # 데이터가 충분할 때만 인덱스 생성
        tbl.create_index(
            metric="cosine",
            vector_column_name="vector",
            num_partitions=16, 
            num_sub_vectors=96
        )
    
    print("⚙️ Building Full-Text Search Index (BM25)...")
    try:
        tbl.create_fts_index("text", replace=True)
    except ImportError:
        print("⚠️ Warning: 'tantivy' not installed. FTS index skipped. (pip install tantivy)")
    
    print(f"✅ Hybrid Indexing Complete.")

if __name__ == "__main__":
    build_hybrid_index()