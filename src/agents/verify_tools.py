
import os
import sys

# Add workspace to path (container-friendly)
sys.path.insert(0, "/workspace/src/agents")
sys.path.insert(0, "/workspace")

try:
    from agent_setup import (
        _fulltext_lpg_impl, 
        _fulltext_rdf_impl, 
        _search_docs_impl, 
        _query_lpg_impl
    )
except ImportError:
    # Handle docker path differences
    sys.path.append(".")
    from agent_setup import (
        _fulltext_lpg_impl, 
        _fulltext_rdf_impl, 
        _search_docs_impl, 
        _query_lpg_impl
    )

def run_verification():
    print("="*60)
    print("🛠️ VERIFYING AGENT TOOLS & FUNCTIONALITY")
    print("="*60)

    # 1. Test LPG Fulltext (New Feature)
    print("\n🔍 1. Testing LPG Fulltext Search ('fulltext_lpg')")
    print("   Query: 'Company' (looking for nodes with 'Company' in name/text)")
    try:
        res = _fulltext_lpg_impl(search_term="Company", top_k=3)
        print(f"   ✅ Result: {res[:300]}...")
    except Exception as e:
        print(f"   ❌ Failed: {e}")

    # 2. Test RDF Fulltext (New Feature)
    print("\n🔍 2. Testing RDF Fulltext Search ('fulltext_rdf')")
    print("   Query: 'Instrument' (looking for ontology terms)")
    try:
        res = _fulltext_rdf_impl(search_term="Instrument", top_k=3)
        print(f"   ✅ Result: {res[:300]}...")
    except Exception as e:
        print(f"   ❌ Failed: {e}")

    # 3. Test Hybrid Search (Updated Feature)
    print("\n🔍 3. Testing Hybrid Search ('search_docs')")
    print("   Query: 'risk factors' (checking fallback or vector search)")
    try:
        res = _search_docs_impl(query="risk factors", top_k=2, search_mode="hybrid")
        print(f"   ✅ Result: {res[:300]}...")
    except Exception as e:
        print(f"   ❌ Failed: {e}")

    # 4. Test Cypher (LPG)
    print("\n📊 4. Testing LPG Cypher ('query_lpg')")
    print("   Query: MATCH (n:Entity) RETURN n.name LIMIT 3")
    try:
        res = _query_lpg_impl(cypher="MATCH (n:Entity) RETURN n.name LIMIT 3")
        print(f"   ✅ Result: {res[:300]}...")
    except Exception as e:
        print(f"   ❌ Failed: {e}")

if __name__ == "__main__":
    run_verification()
