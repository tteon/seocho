#!/usr/bin/env python3
"""
Supply Chain Data Ingestion Script for Seocho Open Source Project
This script demonstrates how to ingest supply chain data into DataHub
and then load it into the GDBMS (DozerDB).
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Any
import requests
from neo4j import GraphDatabase

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SupplyChainDataIngestor:
    def __init__(self, neo4j_uri: str, neo4j_user: str, neo4j_password: str):
        self.neo4j_uri = neo4j_uri
        self.neo4j_user = neo4j_user
        self.neo4j_password = neo4j_password
        self.driver = None
        
    def connect_to_neo4j(self):
        """Establish connection to DozerDB/Neo4j"""
        try:
            self.driver = GraphDatabase.driver(
                self.neo4j_uri, 
                auth=(self.neo4j_user, self.neo4j_password)
            )
            logger.info("Successfully connected to DozerDB")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to DozerDB: {e}")
            return False
    
    def load_sample_data(self, file_path: str) -> Dict[str, Any]:
        """Load sample supply chain data from JSON file"""
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
            logger.info(f"Loaded data with {len(data.get('supply_chain_entities', []))} entities")
            return data
        except Exception as e:
            logger.error(f"Failed to load data: {e}")
            return {}
    
    def create_supply_chain_graph(self, data: Dict[str, Any]):
        """Create supply chain entities and relationships in DozerDB"""
        if not self.driver:
            logger.error("No database connection available")
            return
        
        with self.driver.session() as session:
            # Create suppliers
            for supplier in data.get('supply_chain_entities', []):
                if supplier['type'] == 'supplier':
                    session.write_transaction(self._create_supplier, supplier)
            
            # Create manufacturers
            for manufacturer in data.get('supply_chain_entities', []):
                if manufacturer['type'] == 'manufacturer':
                    session.write_transaction(self._create_manufacturer, manufacturer)
            
            # Create distributors
            for distributor in data.get('supply_chain_entities', []):
                if distributor['type'] == 'distributor':
                    session.write_transaction(self._create_distributor, distributor)
            
            # Create logistics relationships
            for shipment in data.get('logistics_data', []):
                session.write_transaction(self._create_shipment, shipment)
            
            # Create inventory nodes
            for inventory in data.get('inventory_data', []):
                session.write_transaction(self._create_inventory, inventory)
    
    @staticmethod
    def _create_supplier(tx, supplier: Dict[str, Any]):
        query = """
        MERGE (s:Supplier {id: $id})
        SET s.name = $name,
            s.address = $address,
            s.latitude = $lat,
            s.longitude = $lng,
            s.gln = $gln,
            s.capabilities = $capabilities,
            s.certifications = $certifications,
            s.email = $email,
            s.phone = $phone,
            s.created_at = datetime()
        """
        tx.run(query, 
               id=supplier['id'],
               name=supplier['name'],
               address=supplier['location']['address'],
               lat=supplier['location']['coordinates']['lat'],
               lng=supplier['location']['coordinates']['lng'],
               gln=supplier['location']['gln'],
               capabilities=supplier['capabilities'],
               certifications=supplier['certifications'],
               email=supplier['contact']['email'],
               phone=supplier['contact']['phone'])
    
    @staticmethod
    def _create_manufacturer(tx, manufacturer: Dict[str, Any]):
        query = """
        MERGE (m:Manufacturer {id: $id})
        SET m.name = $name,
            m.address = $address,
            m.latitude = $lat,
            m.longitude = $lng,
            m.gln = $gln,
            m.production_lines = $production_lines,
            m.daily_capacity = $daily_capacity,
            m.specialties = $specialties,
            m.created_at = datetime()
        """
        tx.run(query,
               id=manufacturer['id'],
               name=manufacturer['name'],
               address=manufacturer['location']['address'],
               lat=manufacturer['location']['coordinates']['lat'],
               lng=manufacturer['location']['coordinates']['lng'],
               gln=manufacturer['location']['gln'],
               production_lines=manufacturer['production_lines'],
               daily_capacity=manufacturer['daily_capacity'],
               specialties=manufacturer['specialties'])
    
    @staticmethod
    def _create_distributor(tx, distributor: Dict[str, Any]):
        query = """
        MERGE (d:Distributor {id: $id})
        SET d.name = $name,
            d.address = $address,
            d.latitude = $lat,
            d.longitude = $lng,
            d.gln = $gln,
            d.warehouse_size = $warehouse_size,
            d.inventory_turnover = $inventory_turnover,
            d.service_regions = $service_regions,
            d.created_at = datetime()
        """
        tx.run(query,
               id=distributor['id'],
               name=distributor['name'],
               address=distributor['location']['address'],
               lat=distributor['location']['coordinates']['lat'],
               lng=distributor['location']['coordinates']['lng'],
               gln=distributor['location']['gln'],
               warehouse_size=distributor['warehouse_size'],
               inventory_turnover=distributor['inventory_turnover'],
               service_regions=distributor['service_regions'])
    
    @staticmethod
    def _create_shipment(tx, shipment: Dict[str, Any]):
        query = """
        MERGE (sh:Shipment {id: $shipment_id})
        SET sh.description = $description,
            sh.weight = $weight,
            sh.length = $length,
            sh.width = $width,
            sh.height = $height,
            sh.transport_mode = $transport_mode,
            sh.carrier = $carrier,
            sh.estimated_delivery = $estimated_delivery,
            sh.actual_delivery = $actual_delivery,
            sh.created_at = datetime()
        
        MERGE (o:Supplier {id: $origin})
        MERGE (dest:Manufacturer {id: $destination})
        
        MERGE (o)-[:SHIPS_TO]->(sh)
        MERGE (sh)-[:DELIVERS_TO]->(dest)
        """
        tx.run(query,
               shipment_id=shipment['shipment_id'],
               description=shipment['cargo']['description'],
               weight=shipment['cargo']['weight'],
               length=shipment['cargo']['dimensions']['length'],
               width=shipment['cargo']['dimensions']['width'],
               height=shipment['cargo']['dimensions']['height'],
               transport_mode=shipment['transport_mode'],
               carrier=shipment['carrier'],
               estimated_delivery=shipment['estimated_delivery'],
               actual_delivery=shipment['actual_delivery'],
               origin=shipment['origin'],
               destination=shipment['destination'])
    
    @staticmethod
    def _create_inventory(tx, inventory: Dict[str, Any]):
        query = """
        MERGE (i:Inventory {product_id: $product_id})
        SET i.product_name = $product_name,
            i.current_stock = $current_stock,
            i.min_stock = $min_stock,
            i.max_stock = $max_stock,
            i.unit_cost = $unit_cost,
            i.last_restock = datetime($last_restock),
            i.created_at = datetime()
        
        MERGE (d:Distributor {id: $location_id})
        MERGE (s:Manufacturer {id: $supplier})
        
        MERGE (d)-[:STORES]->(i)
        MERGE (s)-[:SUPPLIES]->(i)
        """
        tx.run(query,
               product_id=inventory['product_id'],
               product_name=inventory['product_name'],
               current_stock=inventory['current_stock'],
               min_stock=inventory['min_stock'],
               max_stock=inventory['max_stock'],
               unit_cost=inventory['unit_cost'],
               last_restock=inventory['last_restock'],
               location_id=inventory['location_id'],
               supplier=inventory['supplier'])
    
    def run_queries(self):
        """Run example queries to verify data ingestion"""
        if not self.driver:
            logger.error("No database connection available")
            return
        
        queries = [
            "MATCH (s:Supplier) RETURN s.name, s.address LIMIT 5",
            "MATCH (m:Manufacturer) RETURN m.name, m.daily_capacity LIMIT 5",
            "MATCH (d:Distributor)-[:STORES]->(i:Inventory) RETURN d.name, i.product_name, i.current_stock LIMIT 5",
            "MATCH (s:Supplier)-[:SHIPS_TO]->(sh:Shipment)-[:DELIVERS_TO]->(m:Manufacturer) RETURN s.name, sh.description, m.name LIMIT 3"
        ]
        
        with self.driver.session() as session:
            for i, query in enumerate(queries, 1):
                try:
                    result = session.run(query)
                    records = list(result)
                    logger.info(f"Query {i}: Found {len(records)} records")
                    for record in records:
                        logger.info(f"  {record}")
                except Exception as e:
                    logger.error(f"Query {i} failed: {e}")
    
    def close(self):
        """Close database connection"""
        if self.driver:
            self.driver.close()

def main():
    """Main execution function"""
    logger.info("Starting Supply Chain Data Ingestion")
    
    # Initialize the ingestor
    ingestor = SupplyChainDataIngestor(
        neo4j_uri="bolt://dozerdb:7687",
        neo4j_user="neo4j",
        neo4j_password="thisisakccdemo"
    )
    
    # Connect to database
    if not ingestor.connect_to_neo4j():
        logger.error("Failed to connect to database")
        return
    
    try:
        # Load sample data
        data = ingestor.load_sample_data('/sharepoint/sample_supply_chain_data.json')
        
        if data:
            # Create graph structure
            ingestor.create_supply_chain_graph(data)
            logger.info("Supply chain graph created successfully")
            
            # Run verification queries
            ingestor.run_queries()
            
            logger.info("Data ingestion completed successfully")
        else:
            logger.error("No data to ingest")
    
    finally:
        ingestor.close()

if __name__ == "__main__":
    main()