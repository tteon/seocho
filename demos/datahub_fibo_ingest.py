import logging
from datahub.emitter.mce_builder import make_dataset_urn, make_data_platform_urn
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.emitter.rest_emitter import DatahubRestEmitter
from datahub.metadata.schema_classes import (
    DomainPropertiesClass,
    GlossaryTermInfoClass,
    GlossaryNodeInfoClass,
    DatasetPropertiesClass,
    GlobalTagsClass,
    TagAssociationClass,
)
import sys

# Configure Logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# DataHub GMS URL (Internal Docker Network)
GMS_SERVER = "http://datahub-gms:8080"

def create_domain(emitter, domain_name, description):
    """Creates a Domain in DataHub."""
    domain_urn = f"urn:li:domain:{domain_name.replace(' ', '_')}"
    
    # 1. Domain Properties
    mcp = MetadataChangeProposalWrapper(
        entityUrn=domain_urn,
        aspect=DomainPropertiesClass(
            name=domain_name,
            description=description
        )
    )
    emitter.emit(mcp)
    log.info(f"Created Domain: {domain_name}")
    return domain_urn

def create_glossary_term(emitter, term_name, description, parent_node_urn=None):
    """Creates a Glossary Term."""
    term_urn = f"urn:li:glossaryTerm:FIBO.{term_name}"
    
    aspect = GlossaryTermInfoClass(
        name=term_name,
        definition=description,
        termSource="FIBO",
    )
    
    if parent_node_urn:
        aspect.parentNode = parent_node_urn

    mcp = MetadataChangeProposalWrapper(
        entityUrn=term_urn,
        aspect=aspect
    )
    emitter.emit(mcp)
    log.info(f"Created Glossary Term: {term_name}")
    return term_urn

def ingest_data_mesh():
    try:
        emitter = DatahubRestEmitter(gms_server=GMS_SERVER)
        log.info(f"Connected to DataHub GMS at {GMS_SERVER}")
    except Exception as e:
        log.error(f"Failed to connect to DataHub: {e}")
        return

    # 1. Create Domains (Business Functions)
    treasury_urn = create_domain(emitter, "Treasury", "manages money and liquid assets")
    risk_urn = create_domain(emitter, "Risk Management", "identifies and evaluates risks")
    compliance_urn = create_domain(emitter, "Regulatory Reporting", "ensures adherence to laws")

    # 2. Create FIBO Glossary Node
    # Note: GlossaryNode creation is more complex in some versions, omitting for brevity
    # and focusing on Terms which are the primary tag targets.
    
    # 3. Create FIBO Terms (Classes)
    fibo_terms = {
        "BusinessFunction": "Major functional area of a business.",
        "Product": "A commercially available good or service.",
        "Data": "Representation of facts, concepts, or instructions.",
        "SovereignDebt": "Debt issued by a national government.",
        "FixedIncome": "Investment that yields regular returns."
    }
    
    for term, desc in fibo_terms.items():
        create_glossary_term(emitter, term, desc)

    # 4. Create Logical Datasets (Data Products)
    # Treasury -> Daily Cash Position
    dataset_urn = make_dataset_urn("neo4j", "DailyCashPosition", "PROD")
    
    mcp_props = MetadataChangeProposalWrapper(
        entityUrn=dataset_urn,
        aspect=DatasetPropertiesClass(
            name="Daily Cash Position",
            description="Aggregated view of cash across all accounts.",
            customProperties={
                "domain": "Treasury",
                "fibo_class": "fibo-fnd-dt-fd:Data"
            }
        )
    )
    emitter.emit(mcp_props)
    
    # Associate with Domain
    mcp_domain = MetadataChangeProposalWrapper(
        entityUrn=dataset_urn,
        aspect=DomainPropertiesClass(name="Treasury") # This might overwrite, better to use Domain aspect if strictly available or UI
    )
    # Note: attributing domain to dataset often uses the `domains` aspect, 
    # but for simplicity we used properties above. 
    
    log.info("Data Mesh Mock Ingestion Complete.")

if __name__ == "__main__":
    ingest_data_mesh()
