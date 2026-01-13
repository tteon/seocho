"""
Agent Routing Test Scenarios

3 test scenarios to verify the Manager Agent's orchestration:
1. Integration Test - RDF first, then LPG based on results
2. Lineage/Provenance Test - LPG priority for evidence tracing
3. Conflict Resolution Test - Hierarchy of Truth protocol

Usage:
    python test_agent_routing.py --scenario 1
    python test_agent_routing.py --all
"""
import os
import sys
import argparse
from agents import Runner
from agent_setup import manager_agent

# Import experiment metrics for detailed scoring
try:
    from experiment_metrics import RoutingAccuracy, ContextPrecision, ConflictResolutionScore, ToolCallQuality
    METRICS_AVAILABLE = True
except ImportError:
    METRICS_AVAILABLE = False
    print("⚠️ experiment_metrics not available - basic scoring only")

# Opik tracing (optional - disabled if not accessible)
OPIK_ENABLED = False

# ==========================================
# Test Scenarios
# ==========================================
TEST_SCENARIOS = {
    1: {
        "name": "Integration Test (RDF→LPG Dependency)",
        "description": "Verify: Get definitions from RDF first, then use them to query LPG",
        "expected_tools": ["ask_rdf", "ask_lpg"],
        "expected_order": "ask_rdf before ask_lpg",
        "prompt": """I need a comprehensive risk analysis regarding "Credit Derivatives". 

Please execute the following steps:
1. First, consult the Ontology (RDF) to clearly define what constitutes a "Credit Derivative" and list its subtypes.
2. Based on those definitions, use the Property Graph (LPG) to identify which companies in our database have exposure to these specific instruments.
3. Finally, synthesize this information to explain strictly *how* these companies are connected to the derivatives, citing the specific source documents for verification."""
    },
    
    2: {
        "name": "Lineage & Provenance Test (LPG Priority)",
        "description": "Verify: Uses LPG for evidence tracing instead of simple text search",
        "expected_tools": ["ask_lpg"],
        "expected_order": "ask_lpg prioritized over search_docs",
        "prompt": """I found a claim that "Company Alpha" is facing liquidity issues. 

I need strict evidence, not just a summary. 
Use the Knowledge Graph to trace the exact relationship between "Company Alpha" and "Liquidity Risk". 
Provide the specific 'Source Chunk' (the document segment) linked to this relationship in the graph to prove where this information originated."""
    },
    
    3: {
        "name": "Conflict Resolution Test (Hierarchy of Truth)",
        "description": "Verify: Applies 'Hierarchy of Truth' when sources conflict",
        "expected_tools": ["search_docs", "ask_lpg"],
        "expected_order": "Both called, LPG trusted for structured facts",
        "prompt": """I am getting conflicting information about the "CEO" of "Company Beta". 
Search the general documents to see what is mentioned in the text, and also check the Property Graph for the formally structured node data. 

If the results differ, tell me which one I should trust based on your 'Hierarchy of Truth' protocol, and explain why you chose that source."""
    }
}

# ==========================================
# Test Runner
# ==========================================
def run_routing_test(scenario_id: int) -> dict:
    """Run a single routing test scenario."""
    scenario = TEST_SCENARIOS[scenario_id]
    
    print(f"\n{'='*70}")
    print(f"🧪 Scenario {scenario_id}: {scenario['name']}")
    print(f"{'='*70}")
    print(f"📋 Description: {scenario['description']}")
    print(f"🎯 Expected Tools: {scenario['expected_tools']}")
    print(f"📝 Expected Order: {scenario['expected_order']}")
    print(f"\n🗣️ Prompt:\n{scenario['prompt'][:200]}...")
    print(f"\n{'='*70}")
    print("🚀 Running agent...")
    
    tool_calls = []
    tool_order = []
    retrieved_context = []
    
    try:
        result = Runner.run_sync(manager_agent, scenario["prompt"])
        output = result.final_output if result.final_output else ""
        
        # Extract tool calls
        for step in result.raw_responses:
            if hasattr(step, 'output') and step.output:
                for item in step.output:
                    if hasattr(item, 'tool_calls') and item.tool_calls:
                        for tc in item.tool_calls:
                            tool_name = tc.function.name if hasattr(tc, 'function') else str(tc)
                            tool_calls.append({
                                "tool": tool_name,
                                "tool_name": tool_name,  # Add for metric compatibility
                                "args": tc.function.arguments if hasattr(tc, 'function') else "",
                                "arguments": tc.function.arguments if hasattr(tc, 'function') else ""
                            })
                            tool_order.append(tool_name)
                    
                    # Capture retrieved context
                    if hasattr(item, 'content') and item.content:
                        retrieved_context.append(str(item.content))
        
        # Analyze results
        tools_used = list(set([tc["tool"] for tc in tool_calls]))
        expected_found = all(t in tools_used for t in scenario["expected_tools"])
        context_str = "\n".join(retrieved_context) if retrieved_context else ""
        
        # Print results
        print(f"\n✅ Agent completed!")
        print(f"\n📊 TOOL CALLS ({len(tool_calls)} total):")
        for i, tc in enumerate(tool_calls, 1):
            print(f"   {i}. {tc['tool']}")
            if tc['args']:
                args_preview = tc['args'][:100] + "..." if len(tc['args']) > 100 else tc['args']
                print(f"      Args: {args_preview}")
        
        print(f"\n🔍 BASIC ANALYSIS:")
        print(f"   Tools Used: {tools_used}")
        print(f"   Tool Order: {' → '.join(tool_order) if tool_order else 'None'}")
        print(f"   Expected Tools Found: {'✅ YES' if expected_found else '❌ NO'}")
        
        # === DETAILED METRIC SCORING ===
        if METRICS_AVAILABLE:
            print(f"\n📈 DETAILED METRICS:")
            
            # 1. Routing Accuracy
            routing_metric = RoutingAccuracy()
            routing_score = routing_metric.score(input=scenario["prompt"], tool_calls=tool_calls)
            print(f"   1. Routing Accuracy: {routing_score.value:.2f}")
            print(f"      └─ {routing_score.reason}")
            
            # 2. Context Precision  
            context_metric = ContextPrecision()
            context_score = context_metric.score(input=scenario["prompt"], retrieved_context=context_str)
            print(f"   2. Context Precision: {context_score.value:.2f}")
            print(f"      └─ {context_score.reason}")
            
            # 3. Conflict Resolution
            conflict_metric = ConflictResolutionScore()
            conflict_score = conflict_metric.score(input=scenario["prompt"], output=output, tool_calls=tool_calls)
            print(f"   3. Conflict Resolution: {conflict_score.value:.2f}")
            print(f"      └─ {conflict_score.reason}")
            
            # 4. Tool Call Quality
            quality_metric = ToolCallQuality()
            quality_score = quality_metric.score(tool_calls=tool_calls)
            print(f"   4. Tool Call Quality: {quality_score.value:.2f}")
            print(f"      └─ {quality_score.reason}")
            
            # Overall Score
            overall = (routing_score.value + context_score.value + conflict_score.value + quality_score.value) / 4
            print(f"\n   📊 OVERALL SCORE: {overall:.2f}/1.00")
        
        print(f"\n📝 AGENT OUTPUT (first 500 chars):")
        print(f"   {output[:500]}...")
        
        return {
            "scenario_id": scenario_id,
            "success": True,
            "tools_used": tools_used,
            "tool_order": tool_order,
            "expected_found": expected_found,
            "output": output,
            "retrieved_context": context_str
        }
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return {
            "scenario_id": scenario_id,
            "success": False,
            "error": str(e)
        }


def run_all_tests():
    """Run all test scenarios."""
    print("\n" + "="*70)
    print("🧪 AGENT ROUTING TEST SUITE")
    print("="*70)
    print(f"Running {len(TEST_SCENARIOS)} scenarios...\n")
    
    results = []
    for scenario_id in TEST_SCENARIOS:
        result = run_routing_test(scenario_id)
        results.append(result)
    
    # Summary
    print("\n" + "="*70)
    print("📊 TEST SUMMARY")
    print("="*70)
    
    passed = sum(1 for r in results if r.get("expected_found", False))
    total = len(results)
    
    for r in results:
        scenario = TEST_SCENARIOS[r["scenario_id"]]
        status = "✅ PASS" if r.get("expected_found") else "❌ FAIL"
        print(f"   Scenario {r['scenario_id']}: {status} - {scenario['name']}")
        if r.get("tools_used"):
            print(f"      Tools: {' → '.join(r['tool_order'])}")
    
    print(f"\n   Total: {passed}/{total} scenarios passed")
    print("="*70)
    
    return results


# ==========================================
# Main Entry
# ==========================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent Routing Test Scenarios")
    parser.add_argument("--scenario", type=int, choices=[1, 2, 3], 
                       help="Run specific scenario (1, 2, or 3)")
    parser.add_argument("--all", action="store_true", 
                       help="Run all scenarios")
    
    args = parser.parse_args()
    
    if args.scenario:
        run_routing_test(args.scenario)
    elif args.all:
        run_all_tests()
    else:
        print("Usage:")
        print("  python test_agent_routing.py --scenario 1  # Run scenario 1")
        print("  python test_agent_routing.py --scenario 2  # Run scenario 2")
        print("  python test_agent_routing.py --scenario 3  # Run scenario 3")
        print("  python test_agent_routing.py --all         # Run all scenarios")
        print("\nScenarios:")
        for sid, s in TEST_SCENARIOS.items():
            print(f"  {sid}. {s['name']}")
        print("\n🔍 Results logged to Opik project: 'agent-routing-tests'")
