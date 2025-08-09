# Supply Chain Data Ingestion Engine

## Overview
This engine provides a complete example of ingesting supply chain data into the Seocho open-source project. It demonstrates data flow from structured JSON files through DataHub to the GDBMS (DozerDB).

## Getting Started

### 1. Build and Start Services
```bash
# Start all services
docker compose up -d

# Verify services are running
docker compose ps
```

### 2. Run Data Ingestion
```bash
# Execute the data ingestion script
docker compose exec engine python /app/ingest_data.py

# Or run interactively
docker compose exec engine bash
python /app/ingest_data.py
```

### 3. Query the Data
Access the Neo4j browser at http://localhost:7474
- Username: neo4j
- Password: thisisakccdemo

### 4. View Dashboard
Access NeoDash at http://localhost:5005 for visualizations

## Data Structure

### Entities Created
- **Suppliers**: Manufacturing suppliers with location and capabilities
- **Manufacturers**: Production facilities with capacity information
- **Distributors**: Warehousing and distribution centers
- **Shipments**: Logistics tracking with events and statuses
- **Inventory**: Stock levels and supplier relationships

### Relationships
- Supplier → SHIPS_TO → Shipment → DELIVERS_TO → Manufacturer
- Manufacturer → SUPPLIES → Inventory
- Distributor → STORES → Inventory

## Example Queries

### Basic Entity Queries
```cypher
-- List all suppliers
MATCH (s:Supplier) RETURN s.name, s.address, s.capabilities

-- Show manufacturer capacity
MATCH (m:Manufacturer) RETURN m.name, m.daily_capacity, m.specialties

-- Find inventory by distributor
MATCH (d:Distributor)-[:STORES]->(i:Inventory) 
RETURN d.name, i.product_name, i.current_stock
```

### Advanced Analytics
```cypher
-- Supply chain path analysis
MATCH path = (s:Supplier)-[:SHIPS_TO]->(sh:Shipment)-[:DELIVERS_TO]->(m:Manufacturer)
RETURN s.name as supplier, sh.description as cargo, m.name as manufacturer

-- Geographic distribution
MATCH (n) WHERE n.latitude IS NOT NULL
RETURN n.name, n.address, n.latitude, n.longitude
```

## Custom Data Ingestion

### 1. Prepare Your Data
Place your JSON files in the `/sharepoint/` directory with the same structure as `sample_supply_chain_data.json`.

### 2. Create Custom Recipe
Copy and modify the `datahub_ingestion_recipe.yml` for your specific data format.

### 3. Run Ingestion
```bash
# Copy your data to sharepoint
cp your_data.json /home/ubuntu/lab/seocho/sharepoint/

# Run ingestion with your recipe
docker compose exec engine python /app/ingest_data.py
```

## Architecture

### Components
- **Engine**: Python-based data processing and ingestion
- **DataHub**: Metadata management and lineage tracking
- **DozerDB**: Graph database for supply chain relationships
- **NeoDash**: Visualization and dashboard interface

### Data Flow
1. JSON data files → Engine processing
2. Engine → DataHub metadata ingestion
3. Engine → DozerDB graph creation
4. NeoDash → Visualization and querying

## Troubleshooting

### Common Issues
1. **Connection refused**: Ensure DozerDB is running on port 7687
2. **Authentication failed**: Check Neo4j credentials
3. **Data not appearing**: Verify file paths in sharepoint volume

### Logs
```bash
# Check service logs
docker compose logs engine
docker compose logs dozerdb
```

## Contributing

To add new data types:
1. Extend the JSON schema in sample data
2. Add corresponding entity creation methods in `ingest_data.py`
3. Update the DataHub recipe configuration
4. Test with sample data ingestion