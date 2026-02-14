import os
import json
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from omegaconf import DictConfig

from collector import DataCollector
from data_source import DataSource
from extractor import EntityExtractor
from prompt_manager import PromptManager
from graph_loader import GraphLoader
from linker import EntityLinker
from vector_store import VectorStore
from deduplicator import EntityDeduplicator
from ontology_prompt_bridge import OntologyPromptBridge
from rule_constraints import infer_rules_from_graph, apply_rules_to_graph
from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
from exceptions import PipelineError
from tracing import track

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Aggregated result from a pipeline run."""
    items_processed: int = 0
    items_failed: int = 0
    errors: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.items_failed == 0


class ExtractionPipeline:
    def __init__(
        self,
        cfg: DictConfig,
        data_source: Optional[DataSource] = None,
        ontology_path: Optional[str] = None,
        target_database: str = "kgnormal",
    ):
        self.cfg = cfg
        self.output_dir = "output"
        self.target_database = target_database
        self.enable_rule_constraints = bool(cfg.get("enable_rule_constraints", False))
        os.makedirs(self.output_dir, exist_ok=True)

        # --- Data source (new) or legacy collector ---
        self._data_source = data_source
        self._legacy_collector = (
            DataCollector(use_mock=cfg.get("mock_data", False))
            if data_source is None
            else None
        )

        # --- Ontology-driven prompt bridge ---
        self._ontology = None
        self._ontology_bridge = None
        if ontology_path:
            from ontology.base import Ontology

            self._ontology = Ontology.from_yaml(ontology_path)
            self._ontology_bridge = OntologyPromptBridge(self._ontology)
            logger.info("Loaded ontology '%s' from %s", self._ontology.name, ontology_path)

        self.prompt_manager = PromptManager(cfg)

        self.extractor = EntityExtractor(
            prompt_manager=self.prompt_manager,
            api_key=cfg.openai_api_key,
            model=cfg.model,
        )

        # Graph Loader
        self.graph_loader = GraphLoader(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)

        self.linker = EntityLinker(
            prompt_manager=self.prompt_manager,
            api_key=cfg.openai_api_key,
            model=cfg.model,
        )

        self.vector_store = VectorStore(api_key=cfg.openai_api_key)

        # Deduplicator
        self.deduplicator = EntityDeduplicator(vector_store=self.vector_store)

        # Schema Manager — reuse across items instead of creating per-item
        from schema_manager import SchemaManager

        self._schema_manager = SchemaManager(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)

    @track("pipeline.run")
    def run(self) -> PipelineResult:
        """Execute the full extraction pipeline.

        Returns:
            PipelineResult with counts and per-item error details.
        """
        logger.info("Starting Extraction Pipeline...")
        result = PipelineResult()

        # 1. Collect Data
        if self._data_source is not None:
            raw_data = self._data_source.load()
        else:
            raw_data = self._legacy_collector.collect_raw_data()

        total = len(raw_data) if hasattr(raw_data, '__len__') else '?'

        for idx, item in enumerate(raw_data):
            item_id = item.get("id", f"item_{idx}")
            logger.info("[%s/%s] Processing item %s (%s)...",
                        idx + 1, total, item_id, item.get("category", "unknown"))
            try:
                self.process_item(item)
                result.items_processed += 1
            except (PipelineError, Exception) as e:
                result.items_failed += 1
                result.errors.append({
                    "item_id": item_id,
                    "error_type": type(e).__name__,
                    "message": str(e),
                })
                logger.error("Failed to process item %s: %s", item_id, e)

        # Finalize
        self.vector_store.save_index(self.output_dir)
        self.graph_loader.close()
        self._schema_manager.close()

        logger.info(
            "Pipeline complete: %d success, %d failed out of %s total",
            result.items_processed,
            result.items_failed,
            total,
        )
        return result

    @track("pipeline.process_item")
    def process_item(self, item: dict):
        """Process a single data item: extract -> link -> dedup -> schema -> load.

        Raises:
            PipelineError: On any processing failure for this item.
        """
        # Build extraction context
        context = {"text": item["content"], "category": item.get("category", "general")}
        if self._ontology_bridge:
            context.update(self._ontology_bridge.render_extraction_context())

        # 2. Extract Entities (with ontology context if available)
        extracted_data = self.extractor.extract_entities(
            item["content"], item.get("category", "general"), extra_context=context
        )
        logger.info("Extracted %d nodes.", len(extracted_data.get("nodes", [])))

        # 3. Entity Linking
        logger.debug("Linking entities...")
        extracted_data = self.linker.link_entities(
            extracted_data, category=item.get("category", "general")
        )
        logger.info("Linked entities, count: %d", len(extracted_data.get("nodes", [])))

        # 4. Deduplication
        extracted_data = self.deduplicator.deduplicate(extracted_data)
        logger.info(
            "After dedup: %d nodes, %d relationships",
            len(extracted_data.get("nodes", [])),
            len(extracted_data.get("relationships", [])),
        )

        # 5. Optional SHACL-like rule inference + validation annotation
        if self.enable_rule_constraints:
            ruleset = infer_rules_from_graph(extracted_data)
            extracted_data = apply_rules_to_graph(extracted_data, ruleset)
            logger.info(
                "Rule constraints applied: %d rules, %d failed nodes",
                len(ruleset.rules),
                extracted_data.get("rule_validation_summary", {}).get("failed_nodes", 0),
            )

        # 6. Vector Embedding
        logger.debug("Embedding content for %s...", item["id"])
        self.vector_store.add_document(item["id"], item["content"])

        # 7. Save Intermediate Results
        self._save_results(item["id"], extracted_data)

        # 8. Auto-Sync Schema
        schema_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "conf/schemas/baseline.yaml",
        )
        self._schema_manager.update_schema_from_records(extracted_data, schema_path)
        self._schema_manager.apply_schema(self.target_database, schema_path)

        # 9. Load Graph
        self.graph_loader.load_graph(extracted_data, item["id"])
        logger.info("Loaded graph data for %s", item["id"])

    def _save_results(self, item_id: str, data: dict):
        filename = f"{self.output_dir}/{item_id}_extracted.json"
        with open(filename, "w") as f:
            json.dump(data, f, indent=2)
