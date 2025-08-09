# Supply Chain Analytics Example

This example demonstrates how to use Seocho for comprehensive supply chain data lineage and analytics.

## 📊 What This Example Shows

- **End-to-end data lineage** from suppliers → manufacturers → distributors
- **Real-time inventory tracking** across the supply chain
- **Shipment monitoring** and delivery optimization
- **Quality validation** of supply chain data
- **Interactive visualization** of supply chain networks

## 🗂️ Data Structure

### Entities
- **Suppliers** - Raw material providers
- **Manufacturers** - Production facilities
- **Distributors** - Warehouse and logistics
- **Shipments** - Transportation tracking
- **Inventory** - Stock levels and locations

### Relationships
- `Supplier -[SHIPS_TO]-> Shipment -[DELIVERS_TO]-> Manufacturer`
- `Manufacturer -[SUPPLIES]-> Inventory -[STORES]-> Distributor`

## 🚀 Running This Example

### Step 1: Start Services
```bash
cd seocho
make up
```

### Step 2: Ingest Data
```bash
make ingest-supply-chain
```

### Step 3: Explore Results
- **NeoDash Dashboard**: http://localhost:5005
- **Neo4j Browser**: http://localhost:7474
- **Credentials**: neo4j/thisisakccdemo

## 🔍 Sample Queries

### List All Suppliers
```cypher
MATCH (s:Supplier) 
RETURN s.name, s.address, s.capabilities
LIMIT 10
```

### Track Product Flow
```cypher
MATCH path = (supplier:Supplier)-[:SHIPS_TO*]-(manufacturer)-[:SUPPLIES*]-(distributor)
RETURN path
LIMIT 5
```

### Find Critical Inventory
```cypher
MATCH (d:Distributor)-[:STORES]-(i:Inventory)
WHERE i.current_stock < i.min_stock
RETURN d.name, i.product_name, i.current_stock, i.min_stock
```

## 📈 Visualizations

This example creates several dashboards:

1. **Supply Chain Network** - Interactive graph of all entities
2. **Inventory Levels** - Real-time stock monitoring
3. **Shipment Tracking** - Delivery status and delays
4. **Quality Metrics** - Data completeness and accuracy scores

## 🎯 Customization

### Add Your Own Data
1. Modify `sample_supply_chain_data.json` with your data
2. Update the ingestion script for your schema
3. Create custom Cypher queries for your use case

### Extend the Model
1. Add new entity types in the Python scripts
2. Create new relationships between entities
3. Build custom NeoDash dashboards

## 🔧 Troubleshooting

**Connection Issues:**
- Ensure all services are running: `docker compose ps`
- Check Neo4j logs: `docker compose logs dozerdb`

**Data Issues:**
- Verify JSON format: `python -m json.tool examples/supply_chain/sample_supply_chain_data.json`
- Check ingestion logs: `make logs`

**Visualization Issues:**
- Refresh NeoDash browser
- Verify Neo4j connection settings in NeoDash