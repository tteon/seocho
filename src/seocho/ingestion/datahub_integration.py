#!/usr/bin/env python3
"""
DataHub Integration Script for Seocho Project
This script connects DataHub glossary ingestion with Neo4j graph integration
"""

import os
import json
import logging
from datetime import datetime
from neo4j import GraphDatabase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DataHubGlossaryIntegrator:
    def __init__(self, neo4j_uri: str, neo4j_user: str, neo4j_password: str):
        self.neo4j_uri = neo4j_uri
        self.neo4j_user = neo4j_user
        self.neo4j_password = neo4j_password
        self.driver = None
        
    def connect(self):
        """Establish connection to DozerDB"""
        try:
            self.driver = GraphDatabase.driver(
                self.neo4j_uri, 
                auth=(self.neo4j_user, self.neo4j_password)
            )
            logger.info("Connected to DozerDB")
            return True
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False
    
    def load_glossary_data(self, file_path: str) -> dict:
        """Load glossary terms in Palantir Foundry ontology format"""
        # Using Palantir Foundry ontology format: <username>(entity)
        glossary_terms = [
            {
                "name": "seocho(consignor_role)",
                "display_name": "consignor_role",
                "description": "Imported from RDF: consignor role",
                "category": "supply_chain_role",
                "source_uri": "https://spec.industrialontologies.org/ontology/supplychain/SupplyChain/ConsignorRole",
                "owner": "seocho"
            },
            {
                "name": "seocho(lot_number)",
                "display_name": "lot_number", 
                "description": "Imported from RDF: lot number",
                "category": "identification",
                "source_uri": "https://spec.industrialontologies.org/ontology/supplychain/SupplyChain/LotNumber",
                "owner": "seocho"
            },
            {
                "name": "seocho(global_location_number)",
                "display_name": "global_location_number",
                "description": "Imported from RDF: global location number",
                "category": "identification", 
                "source_uri": "https://spec.industrialontologies.org/ontology/supplychain/SupplyChain/GlobalLocationNumber",
                "owner": "seocho"
            },
            {
                "name": "seocho(supply_chain_process)",
                "display_name": "supply_chain_process",
                "description": "Imported from RDF: supply chain process",
                "category": "process",
                "source_uri": "https://spec.industrialontologies.org/ontology/supplychain/SupplyChain/SupplyChainProcess",
                "owner": "seocho"
            },
            {
                "name": "seocho(freight_forwarder)",
                "display_name": "freight_forwarder",
                "description": "Imported from RDF: freight forwarder",
                "category": "agent",
                "source_uri": "https://spec.industrialontologies.org/ontology/supplychain/SupplyChain/FreightForwarder",
                "owner": "seocho"
            },
            {
                "name": "alice(bill_of_lading)",
                "display_name": "bill_of_lading",
                "description": "Imported from RDF: bill of lading",
                "category": "document",
                "source_uri": "https://spec.industrialontologies.org/ontology/supplychain/SupplyChain/BillOfLading",
                "owner": "alice"
            },
            {
                "name": "bob(supplier_evaluation_process)",
                "display_name": "supplier_evaluation_process",
                "description": "Imported from RDF: supplier evaluation process",
                "category": "process",
                "source_uri": "https://spec.industrialontologies.org/ontology/supplychain/SupplyChain/SupplierEvaluationProcess",
                "owner": "bob"
            },
            {
                "name": "carol(manufacturing_service)",
                "display_name": "manufacturing_service",
                "description": "Imported from RDF: manufacturing service",
                "category": "service",
                "source_uri": "https://spec.industrialontologies.org/ontology/supplychain/service/ServiceOntology/ManufacturingService",
                "owner": "carol"
            }
        ]
        
        return {"glossary_terms": glossary_terms}
    
    def create_glossary_graph(self, data: dict):
        """Create glossary entities in Neo4j"""
        if not self.driver:
            logger.error("No database connection")
            return
        
        with self.driver.session() as session:
            for term in data.get("glossary_terms", []):
                session.execute_write(self._create_glossary_term, term)
    
    @staticmethod
    def _create_glossary_term(tx, term: dict):
        query = """
        MERGE (g:GlossaryTerm {name: $name})
        SET g.display_name = $display_name,
            g.description = $description,
            g.category = $category,
            g.source_uri = $source_uri,
            g.owner = $owner,
            g.ontology_format = 'palantir_foundry',
            g.created_at = datetime(),
            g.ingested_from = 'datahub_glossary'
        """
        tx.run(query, **term)
    
    def link_glossary_to_entities(self):
        """Link glossary terms to existing supply chain entities"""
        if not self.driver:
            logger.error("No database connection")
            return
        
        with self.driver.session() as session:
            # Link freight forwarder glossary to freight forwarder entities
            session.execute_write(self._link_glossary_to_entities)
    
    @staticmethod
    def _link_glossary_to_entities(tx):
        query = """
        MATCH (g:GlossaryTerm {name: 'freight_forwarder'})
        MATCH (ff:FreightForwarder) 
        WHERE ff.name IS NOT NULL
        MERGE (g)-[:DEFINES]->(ff)
        RETURN count(ff) as linked_entities
        """
        result = tx.run(query)
        for record in result:
            logger.info(f"Linked {record['linked_entities']} entities to glossary")
    
    def list_glossary_terms(self):
        """List all glossary terms in Palantir format"""
        if not self.driver:
            logger.error("No database connection")
            return
        
        with self.driver.session() as session:
            query = """
            MATCH (g:GlossaryTerm)
            RETURN g.name as name, g.display_name as display_name, g.description as description, 
                   g.category as category, g.owner as owner
            ORDER BY g.owner, g.name
            """
            result = session.run(query)
            terms = []
            for record in result:
                terms.append({
                    "name": record["name"],
                    "display_name": record["display_name"],
                    "description": record["description"],
                    "category": record["category"],
                    "owner": record["owner"]
                })
            return terms
    
    def close(self):
        if self.driver:
            self.driver.close()

def main():
    """Main integration function"""
    logger.info("Starting DataHub Glossary Integration")
    
    integrator = DataHubGlossaryIntegrator(
        neo4j_uri="bolt://dozerdb:7687",
        neo4j_user="neo4j",
        neo4j_password="thisisakccdemo"
    )
    
    if not integrator.connect():
        return
    
    try:
        # Load and process glossary data
        data = integrator.load_glossary_data("/datahub/recipe_glossary.yml")
        
        # Create glossary graph
        integrator.create_glossary_graph(data)
        logger.info("Glossary terms created successfully")
        
        # Link to existing entities
        integrator.link_glossary_to_entities()
        
        # List glossary terms
        terms = integrator.list_glossary_terms()
        logger.info(f"Found {len(terms)} glossary terms")
        for term in terms:
            logger.info(f"  - {term['name']} ({term['category']})")
            
        logger.info("DataHub glossary integration completed")
    
    finally:
        integrator.close()

if __name__ == "__main__":
    main()