"""
Enhanced Experiment Metrics for Graph Agent Evaluation

Three core evaluation dimensions:
1. Routing Accuracy - Did agent call correct tools for question intent?
2. Context Precision - Was retrieved context relevant to the query?
3. Conflict Resolution - Did agent follow Hierarchy of Truth principles?
"""
from opik.evaluation.metrics import base_metric, score_result
import re


class RoutingAccuracy(base_metric.BaseMetric):
    """
    Evaluates if the agent routed to the correct tools based on question intent.
    
    Intent Detection Rules:
    - Definition/Concept questions → ask_rdf expected
    - Fact/Number questions → ask_lpg expected
    - Evidence/Source questions → ask_lpg expected (provenance)
    - General context → search_docs acceptable
    """
    
    INTENT_PATTERNS = {
        "definition": {
            "keywords": ["what is", "define", "meaning of", "what are", "explain", "is a", "type of", "classification"],
            "expected_tool": "ask_rdf",
            "fallback": "search_docs"
        },
        "fact_number": {
            "keywords": ["revenue", "income", "eps", "profit", "how much", "what was the", "amount", "number", "percentage", "growth"],
            "expected_tool": "ask_lpg",
            "fallback": None
        },
        "provenance": {
            "keywords": ["evidence", "source", "prove", "trace", "lineage", "document", "chunk", "where did", "citation"],
            "expected_tool": "ask_lpg",
            "fallback": None
        },
        "relationship": {
            "keywords": ["connected to", "related to", "relationship", "linked", "associated with", "between"],
            "expected_tool": "ask_lpg",
            "fallback": "ask_rdf"
        },
        "conflict": {
            "keywords": ["conflicting", "differ", "discrepancy", "trust", "which one"],
            "expected_tools": ["ask_lpg", "search_docs"],  # Both needed
            "fallback": None
        }
    }
    
    def __init__(self, name: str = "routing_accuracy"):
        self.name = name
    
    def _detect_intent(self, query: str) -> tuple:
        """Detect query intent and expected tools."""
        query_lower = query.lower()
        detected_intents = []
        expected_tools = set()
        
        for intent_name, config in self.INTENT_PATTERNS.items():
            if any(kw in query_lower for kw in config["keywords"]):
                detected_intents.append(intent_name)
                if "expected_tools" in config:
                    expected_tools.update(config["expected_tools"])
                else:
                    expected_tools.add(config["expected_tool"])
                    if config.get("fallback"):
                        expected_tools.add(config["fallback"])
        
        return detected_intents, list(expected_tools)
    
    def score(self, input, tool_calls, **kwargs):
        """
        Score routing accuracy.
        
        Args:
            input: User query
            tool_calls: List of tool calls made by agent
        """
        try:
            # Detect intent
            intents, expected_tools = self._detect_intent(str(input))
            
            if not intents:
                return score_result.ScoreResult(
                    name=self.name,
                    value=0.5,
                    reason="Intent unclear - any routing acceptable"
                )
            
            # Extract tools used
            tools_used = set()
            for call in tool_calls:
                tool_name = call.get("tool_name", call.get("tool", call.get("function_name", "")))
                tools_used.add(tool_name.lower())
            
            if not tools_used:
                return score_result.ScoreResult(
                    name=self.name,
                    value=0.0,
                    reason=f"No tools called. Expected: {expected_tools} for intents: {intents}"
                )
            
            # Calculate accuracy
            expected_set = set(t.lower() for t in expected_tools)
            correct_calls = tools_used & expected_set
            
            if correct_calls:
                score = len(correct_calls) / len(expected_set)
                return score_result.ScoreResult(
                    name=self.name,
                    value=min(score, 1.0),
                    reason=f"Intent: {intents} | Expected: {expected_tools} | Used: {list(tools_used)} | Match: {len(correct_calls)}/{len(expected_set)}"
                )
            else:
                return score_result.ScoreResult(
                    name=self.name,
                    value=0.0,
                    reason=f"Wrong routing. Intent: {intents} | Expected: {expected_tools} | Used: {list(tools_used)}"
                )
                
        except Exception as e:
            return score_result.ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"Scoring error: {e}"
            )


class ContextPrecision(base_metric.BaseMetric):
    """
    Evaluates if the retrieved context is actually relevant to the query.
    Uses keyword overlap and domain-specific term matching.
    """
    
    # FIBO financial domain terms
    FINANCIAL_TERMS = {
        "revenue", "income", "asset", "liability", "equity", "derivative", 
        "bond", "stock", "share", "dividend", "portfolio", "risk", "hedge",
        "liquidity", "credit", "cdo", "cds", "swap", "option", "futures",
        "obligation", "payment", "maturity", "yield", "interest", "rate"
    }
    
    def __init__(self, name: str = "context_precision"):
        self.name = name
    
    def score(self, input, retrieved_context, output=None, **kwargs):
        """
        Score context precision.
        
        Args:
            input: User query
            retrieved_context: What was retrieved from databases
            output: Agent's final output (optional, for verification)
        """
        try:
            if not retrieved_context:
                return score_result.ScoreResult(
                    name=self.name,
                    value=0.0,
                    reason="No context retrieved"
                )
            
            input_lower = str(input).lower()
            context_lower = str(retrieved_context).lower()
            
            # 1. Keyword overlap score
            input_words = set(re.findall(r'\b\w+\b', input_lower))
            context_words = set(re.findall(r'\b\w+\b', context_lower))
            
            # Remove stopwords
            stopwords = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'for', 'of', 'to', 'in', 
                        'on', 'with', 'and', 'or', 'what', 'how', 'why', 'when', 'where', 'i', 
                        'you', 'it', 'this', 'that', 'be', 'have', 'do', 'at', 'by', 'from'}
            input_words -= stopwords
            
            keyword_overlap = len(input_words & context_words)
            keyword_score = min(keyword_overlap / max(len(input_words), 1), 1.0)
            
            # 2. Financial domain relevance
            financial_in_context = context_words & self.FINANCIAL_TERMS
            financial_in_query = input_words & self.FINANCIAL_TERMS
            
            domain_score = 0.0
            if financial_in_query:
                domain_overlap = len(financial_in_context & financial_in_query)
                domain_score = domain_overlap / len(financial_in_query)
            elif financial_in_context:
                domain_score = 0.5  # Context has financial terms even if query doesn't
            
            # 3. Combined score (weighted)
            final_score = (keyword_score * 0.6) + (domain_score * 0.4)
            
            return score_result.ScoreResult(
                name=self.name,
                value=final_score,
                reason=f"Keyword overlap: {keyword_overlap} ({keyword_score:.2f}) | Domain terms: {len(financial_in_context)} ({domain_score:.2f})"
            )
            
        except Exception as e:
            return score_result.ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"Scoring error: {e}"
            )


class ConflictResolutionScore(base_metric.BaseMetric):
    """
    Evaluates if the agent follows the Hierarchy of Truth when sources conflict.
    
    Hierarchy of Truth:
    1. For Definitions/Types → Trust RDF over others
    2. For Numbers/Relations/Lineage → Trust LPG over others
    3. For General Descriptions → Use search_docs to fill gaps
    """
    
    def __init__(self, name: str = "conflict_resolution"):
        self.name = name
    
    def _detect_query_type(self, query: str) -> str:
        """Detect if query is about definitions, numbers, or general."""
        query_lower = query.lower()
        
        # Definition indicators
        if any(kw in query_lower for kw in ["what is", "define", "meaning", "type of", "is a"]):
            return "definition"
        
        # Number/fact indicators
        if any(kw in query_lower for kw in ["revenue", "income", "amount", "how much", "number", "percentage", "ceo", "who is"]):
            return "fact"
        
        # Conflict indicators
        if any(kw in query_lower for kw in ["conflict", "differ", "discrepancy", "trust", "which"]):
            return "conflict"
        
        return "general"
    
    def score(self, input, output, tool_calls, **kwargs):
        """
        Score conflict resolution behavior.
        
        Args:
            input: User query
            output: Agent's final output
            tool_calls: List of tool calls made
        """
        try:
            query_type = self._detect_query_type(str(input))
            output_lower = str(output).lower()
            
            # Check which tools were used
            tools_used = set()
            for call in tool_calls:
                tool_name = call.get("tool_name", call.get("tool", call.get("function_name", "")))
                tools_used.add(tool_name.lower())
            
            # For conflict resolution queries
            if query_type == "conflict":
                # Check if both graph and text sources were consulted
                used_graph = "ask_lpg" in tools_used
                used_text = "search_docs" in tools_used
                
                if not (used_graph or used_text):
                    return score_result.ScoreResult(
                        name=self.name,
                        value=0.0,
                        reason="No sources consulted for conflict resolution"
                    )
                
                # Check if output mentions trust hierarchy
                mentions_hierarchy = any(phrase in output_lower for phrase in [
                    "trust", "reliable", "structured", "graph", "lpg", "authoritative",
                    "prefer", "priority", "hierarchy", "source"
                ])
                
                score = 0.5
                if used_graph and used_text:
                    score = 0.75  # Both consulted
                if mentions_hierarchy:
                    score = 1.0  # Explains reasoning
                
                return score_result.ScoreResult(
                    name=self.name,
                    value=score,
                    reason=f"Conflict query: Graph={used_graph}, Text={used_text}, Hierarchy explained={mentions_hierarchy}"
                )
            
            # For fact queries
            elif query_type == "fact":
                if "ask_lpg" in tools_used:
                    return score_result.ScoreResult(
                        name=self.name,
                        value=1.0,
                        reason="Correctly prioritized LPG for factual query"
                    )
                else:
                    return score_result.ScoreResult(
                        name=self.name,
                        value=0.5,
                        reason=f"Fact query but LPG not prioritized. Used: {tools_used}"
                    )
            
            # For definition queries
            elif query_type == "definition":
                if "ask_rdf" in tools_used:
                    return score_result.ScoreResult(
                        name=self.name,
                        value=1.0,
                        reason="Correctly prioritized RDF for definition query"
                    )
                else:
                    return score_result.ScoreResult(
                        name=self.name,
                        value=0.5,
                        reason=f"Definition query but RDF not prioritized. Used: {tools_used}"
                    )
            
            # General queries
            return score_result.ScoreResult(
                name=self.name,
                value=0.75,
                reason=f"General query - any routing acceptable. Used: {tools_used}"
            )
            
        except Exception as e:
            return score_result.ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"Scoring error: {e}"
            )


class ToolCallQuality(base_metric.BaseMetric):
    """
    Evaluates the quality of tool call arguments (e.g., Cypher query syntax, search parameters).
    """
    
    def __init__(self, name: str = "tool_call_quality"):
        self.name = name
    
    def score(self, tool_calls, **kwargs):
        """
        Score tool call quality.
        """
        try:
            if not tool_calls:
                return score_result.ScoreResult(
                    name=self.name,
                    value=0.0,
                    reason="No tool calls to evaluate"
                )
            
            total_score = 0.0
            evaluations = []
            
            for call in tool_calls:
                tool_name = call.get("tool_name", call.get("tool", call.get("function_name", "")))
                args = call.get("arguments", call.get("args", ""))
                
                if "lpg" in tool_name.lower() or "rdf" in tool_name.lower():
                    # Evaluate Cypher query
                    if "MATCH" in str(args) and "RETURN" in str(args):
                        total_score += 1.0
                        evaluations.append(f"{tool_name}: Valid Cypher")
                    else:
                        total_score += 0.5
                        evaluations.append(f"{tool_name}: Incomplete Cypher")
                        
                elif "search" in tool_name.lower():
                    # Evaluate search query
                    if len(str(args)) > 5:
                        total_score += 1.0
                        evaluations.append(f"{tool_name}: Valid query")
                    else:
                        total_score += 0.5
                        evaluations.append(f"{tool_name}: Short query")
                else:
                    total_score += 0.75
                    evaluations.append(f"{tool_name}: Unknown tool type")
            
            avg_score = total_score / len(tool_calls)
            
            return score_result.ScoreResult(
                name=self.name,
                value=avg_score,
                reason=" | ".join(evaluations)
            )
            
        except Exception as e:
            return score_result.ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"Scoring error: {e}"
            )
