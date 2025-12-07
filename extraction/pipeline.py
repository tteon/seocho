import os
import json
from omegaconf import DictConfig
from collector import DataCollector
from metadata import MetadataHandler
from extractor import EntityExtractor
from prompt_manager import PromptManager
from graph_loader import GraphLoader
from linker import EntityLinker
from vector_store import VectorStore

class ExtractionPipeline:
    def __init__(self, cfg: DictConfig):
        self.cfg = cfg
        self.output_dir = "output"
        os.makedirs(self.output_dir, exist_ok=True)
        
        # Initialize Components
        self.collector = DataCollector(use_mock=cfg.get("mock_data", False))
        self.metadata_handler = MetadataHandler()
        self.prompt_manager = PromptManager(cfg)
        
        self.extractor = EntityExtractor(
            prompt_manager=self.prompt_manager,
            api_key=cfg.openai_api_key,
            model=cfg.model
        )
        
        # Graph Loader
        neo4j_uri = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
        neo4j_user = os.getenv("NEO4J_USER", "neo4j")
        neo4j_password = os.getenv("NEO4J_PASSWORD", "password")
        self.graph_loader = GraphLoader(neo4j_uri, neo4j_user, neo4j_password)
        
        self.linker = EntityLinker(
            prompt_manager=self.prompt_manager,
            api_key=cfg.openai_api_key,
            model=cfg.model
        )
        
        self.vector_store = VectorStore(api_key=cfg.openai_api_key)

    def run(self):
        """
        Executes the full extraction pipeline.
        """
        print("Starting Extraction Pipeline...")
        
        # 1. Collect Data
        raw_data = self.collector.collect_raw_data()
        
        for item in raw_data:
            print(f"Processing item {item['id']} ({item['category']})...")
            self.process_item(item)
            
        # Finalize
        self.vector_store.save_index(self.output_dir)
        self.graph_loader.close()
        print("Pipeline execution complete.")

    def process_item(self, item: dict):
        """
        Processes a single data item through extraction, linking, embedding, and loading.
        """
        try:
            # 2. Extract Entities
            extracted_data = self.extractor.extract_entities(item['content'], item.get('category', 'general'))
            print(f"Extracted {len(extracted_data.get('nodes', []))} nodes.")
            
            # 3. Entity Linking
            print("Linking entities...")
            extracted_data = self.linker.link_entities(extracted_data, category=item.get('category', 'general'))
            print(f"Linked entities, count: {len(extracted_data.get('nodes', []))}")
            
            # 4. Metadata
            self.metadata_handler.emit_metadata(item)
            
            # 5. Vector Embedding
            print(f"Embedding content for {item['id']}...")
            self.vector_store.add_document(item['id'], item['content'])

            # 6. Save Intermediate Results
            self._save_results(item['id'], extracted_data)
            
            # 7. Load Graph
            self.graph_loader.load_graph(extracted_data, item['id'])
            print(f"Loaded graph data for {item['id']}")
            
        except Exception as e:
            print(f"Error processing item {item['id']}: {e}")

    def _save_results(self, item_id: str, data: dict):
        filename = f"{self.output_dir}/{item_id}_extracted.json"
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)
