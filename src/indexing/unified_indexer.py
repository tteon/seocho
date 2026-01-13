"""
Unified Indexer
Orchestrates both LanceDB and Neo4j indexing in a single pipeline.
"""
from src.indexing.lancedb_indexer import LanceDBIndexer
from src.indexing.neo4j_indexer import Neo4jIndexer


class UnifiedIndexer:
    """
    Coordinates indexing across LanceDB (vector) and Neo4j (graph).
    """
    
    def __init__(self):
        self.lancedb_indexer = LanceDBIndexer()
        self.neo4j_indexer = Neo4jIndexer()
    
    def build_all(
        self,
        opik_dataset: str = "fibo-evaluation-dataset",
        traces_path: str = None
    ):
        """
        Build both vector and graph indexes.
        
        Args:
            opik_dataset: Name of Opik dataset for LanceDB
            traces_path: Path to traces JSON for Neo4j
        """
        print("=" * 70)
        print("🚀 Unified Indexing - Starting")
        print("=" * 70)
        
        # 1. Build LanceDB index
        print("\n📊 Phase 1: Building LanceDB Vector Index")
        print("-" * 50)
        self.lancedb_indexer.connect()
        try:
            self.lancedb_indexer.build_from_opik(opik_dataset)
        except Exception as e:
            print(f"⚠️ Opik unavailable: {e}")
            print("Trying local JSON files...")
            json_paths = [
                "/workspace/output/opik_exports/fibo-evaluation-dataset_export.json",
                "/workspace/kgbuild-traces.json",
            ]
            self.lancedb_indexer.build_from_json(json_paths)
        
        # 2. Build Neo4j indexes
        print("\n📊 Phase 2: Building Neo4j Graph Indexes")
        print("-" * 50)
        try:
            self.neo4j_indexer.connect()
            self.neo4j_indexer.build_from_traces(traces_path)
        finally:
            self.neo4j_indexer.close()
        
        print("\n" + "=" * 70)
        print("✅ Unified Indexing Complete!")
        print("=" * 70)


def build_all_indexes():
    """CLI entry point for unified indexing."""
    indexer = UnifiedIndexer()
    indexer.build_all()


if __name__ == "__main__":
    build_all_indexes()
