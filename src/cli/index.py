"""
CLI: Index
Build vector and graph indexes.

Usage:
    python -m src.cli.index --lancedb          # LanceDB only
    python -m src.cli.index --neo4j            # Neo4j only
    python -m src.cli.index --all              # Both indexes
"""
import argparse
from src.indexing.lancedb_indexer import LanceDBIndexer, build_lancedb_index
from src.indexing.neo4j_indexer import Neo4jIndexer, build_neo4j_index
from src.indexing.unified_indexer import UnifiedIndexer, build_all_indexes


def main():
    parser = argparse.ArgumentParser(description="Build search indexes")
    parser.add_argument("--lancedb", action="store_true", help="Build LanceDB vector index")
    parser.add_argument("--neo4j", action="store_true", help="Build Neo4j graph index")
    parser.add_argument("--all", action="store_true", help="Build all indexes")
    parser.add_argument("--traces", type=str, help="Path to traces JSON for Neo4j")
    parser.add_argument("--dataset", type=str, default="fibo-evaluation-dataset", 
                        help="Opik dataset name for LanceDB")
    
    args = parser.parse_args()
    
    if args.all:
        print("🚀 Building all indexes...")
        build_all_indexes()
    elif args.lancedb:
        print("🚀 Building LanceDB index...")
        indexer = LanceDBIndexer()
        indexer.connect()
        indexer.build_from_opik(args.dataset)
    elif args.neo4j:
        print("🚀 Building Neo4j index...")
        build_neo4j_index(args.traces)
    else:
        print("Usage:")
        print("  python -m src.cli.index --lancedb")
        print("  python -m src.cli.index --neo4j")
        print("  python -m src.cli.index --all")


if __name__ == "__main__":
    main()
