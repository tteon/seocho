"""
Custom Metrics for Graph Agent Evaluation

These metrics evaluate agent step-level quality:
- RetrievalQuality: Was the right tool (LPG/RDF) selected?
- RetrievalRelevance: Was the retrieved context relevant?
"""
from opik.evaluation.metrics import base_metric, score_result


class RetrievalQuality(base_metric.BaseMetric):
    """
    Evaluates if the agent selected the appropriate retrieval tool.
    Compares actual tool calls against expected tools.
    """
    
    def __init__(self, name: str = "retrieval_quality"):
        self.name = name
    
    def score(self, tool_calls, expected_tools=None, **kwargs):
        """
        Score tool selection quality.
        
        Args:
            tool_calls: List of actual tool calls made by agent
            expected_tools: List of expected tool names (optional)
        """
        try:
            if not tool_calls:
                return score_result.ScoreResult(
                    name=self.name,
                    value=0.0,
                    reason="No tool calls made by agent"
                )
            
            # Extract unique tools used
            tools_used = set()
            for call in tool_calls:
                tool_name = call.get("tool_name", call.get("function_name", "unknown"))
                tools_used.add(tool_name)
            
            tools_str = ", ".join(sorted(tools_used))
            
            # If expected tools provided, compare
            if expected_tools:
                expected_set = set(expected_tools)
                overlap = tools_used & expected_set
                score = len(overlap) / len(expected_set) if expected_set else 0.0
                
                return score_result.ScoreResult(
                    name=self.name,
                    value=score,
                    reason=f"Tools used: [{tools_str}]. Expected: {expected_tools}. Match: {len(overlap)}/{len(expected_set)}"
                )
            
            # Without expected tools, score based on whether tools were used
            return score_result.ScoreResult(
                name=self.name,
                value=1.0,
                reason=f"Tools used: [{tools_str}]"
            )
            
        except Exception as e:
            return score_result.ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"Scoring error: {e}"
            )


class RetrievalRelevance(base_metric.BaseMetric):
    """
    Evaluates if the retrieved context is relevant to the query.
    Uses simple keyword overlap scoring.
    """
    
    def __init__(self, name: str = "retrieval_relevance"):
        self.name = name
    
    def score(self, input, retrieved_context, reference=None, **kwargs):
        """
        Score retrieval relevance.
        
        Args:
            input: User query
            retrieved_context: What the agent retrieved from databases
            reference: Ground truth context (optional)
        """
        try:
            if not retrieved_context:
                return score_result.ScoreResult(
                    name=self.name,
                    value=0.0,
                    reason="No context retrieved"
                )
            
            # Extract keywords from input
            input_lower = str(input).lower()
            context_lower = str(retrieved_context).lower()
            
            # Simple keyword analysis
            input_words = set(input_lower.split())
            context_words = set(context_lower.split())
            
            # Remove stopwords
            stopwords = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'for', 'of', 'to', 'in', 'on', 'with', 'and', 'or', 'what', 'how', 'why', 'when', 'where'}
            input_words -= stopwords
            
            # Calculate overlap
            overlap = input_words & context_words
            score = len(overlap) / len(input_words) if input_words else 0.0
            
            # If reference provided, also check against that
            if reference:
                ref_lower = str(reference).lower()
                ref_words = set(ref_lower.split()) - stopwords
                ref_overlap = ref_words & context_words
                ref_score = len(ref_overlap) / len(ref_words) if ref_words else 0.0
                score = (score + ref_score) / 2
            
            return score_result.ScoreResult(
                name=self.name,
                value=min(score, 1.0),  # Cap at 1.0
                reason=f"Query-context overlap: {len(overlap)} keywords. Retrieved {len(context_words)} words."
            )
            
        except Exception as e:
            return score_result.ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"Scoring error: {e}"
            )


class DatabaseSelectionQuality(base_metric.BaseMetric):
    """
    Evaluates if the agent chose the right database (LPG vs RDF).
    LPG for facts/numbers, RDF for semantics/definitions.
    """
    
    def __init__(self, name: str = "database_selection"):
        self.name = name
    
    def score(self, input, tool_calls, **kwargs):
        """
        Score database selection based on query type.
        """
        try:
            input_lower = str(input).lower()
            
            # Determine expected database based on query keywords
            lpg_keywords = {'revenue', 'income', 'eps', 'profit', 'cost', 'amount', 'number', 'value', 'how much', 'what is the', '2023', '2024', '2022'}
            rdf_keywords = {'what is', 'define', 'meaning', 'type', 'category', 'ontology', 'class', 'hierarchy', 'is a'}
            
            expects_lpg = any(kw in input_lower for kw in lpg_keywords)
            expects_rdf = any(kw in input_lower for kw in rdf_keywords)
            
            # Extract tools used
            tools_used = set()
            for call in tool_calls:
                tool_name = call.get("tool_name", call.get("function_name", ""))
                tools_used.add(tool_name.lower())
            
            used_lpg = 'ask_lpg' in tools_used or 'query_lpg' in tools_used
            used_rdf = 'ask_rdf' in tools_used or 'query_rdf' in tools_used
            
            # Score based on alignment
            if expects_lpg and used_lpg:
                return score_result.ScoreResult(
                    name=self.name,
                    value=1.0,
                    reason="Correctly used LPG for factual query"
                )
            elif expects_rdf and used_rdf:
                return score_result.ScoreResult(
                    name=self.name,
                    value=1.0,
                    reason="Correctly used RDF for semantic query"
                )
            elif not expects_lpg and not expects_rdf:
                return score_result.ScoreResult(
                    name=self.name,
                    value=0.5,
                    reason="Query type unclear, any database choice acceptable"
                )
            else:
                return score_result.ScoreResult(
                    name=self.name,
                    value=0.0,
                    reason=f"Mismatch: expected {'LPG' if expects_lpg else 'RDF'}, used {tools_used}"
                )
                
        except Exception as e:
            return score_result.ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"Scoring error: {e}"
            )
