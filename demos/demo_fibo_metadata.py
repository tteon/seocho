import time
from datahub.emitter.mce_builder import (
    make_dataset_urn,
    make_user_urn,
    make_data_platform_urn,
    make_tag_urn,
)
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.emitter.rest_emitter import DatahubRestEmitter
from datahub.metadata.schema_classes import (
    SchemaMetadataClass,
    SchemaFieldClass,
    SchemaFieldDataTypeClass,
    StringTypeClass,
    NumberTypeClass,
    DateTypeClass,
    DatasetPropertiesClass,
    OwnershipClass,
    OwnerClass,
    OwnershipTypeClass,
    GlobalTagsClass,
    TagAssociationClass,
    OtherSchemaClass,
)

# 1. Configuration: Point to DataHub GMS (Docker Network)
GMS_SERVER = "http://datahub-gms:8080"
emitter = DatahubRestEmitter(gms_server=GMS_SERVER)

# 2. Define the Entity URNs
# We model a Bond Security Master record coming from a 'SecurityMaster' platform
platform = "security_master"
dataset_name = "bonds/us_treasury/US1234567890"
dataset_urn = make_dataset_urn(platform, dataset_name)

# Role: Compliance Officer (Administrative Owner)
user_urn = make_user_urn("compliance_officer") 

print(f"Preparing metadata for Bond Record: {dataset_urn}")

# =================================================================
# SECTION A: Administrative Metadata (The Red Box)
# Maps to Ownership and Governance Properties
# =================================================================

# 1. Ownership (Graph Edge: Compliance Officer -> OWNS -> Bond Record)
ownership_aspect = OwnershipClass(
    owners=[
        OwnerClass(
            owner=user_urn,
            type=OwnershipTypeClass.DATA_STEWARD, # Responsible for data quality
        )
    ]
)

# 2. Administrative Properties (Key-Value pairs for governance/status)
properties_aspect = DatasetPropertiesClass(
    name="US Treasury Bond 2030",
    description="Master record for US Treasury Note expiring 2030",
    customProperties={
        "jurisdiction": "US",
        "tradable_status": "Active",
        "regulatory_class": "HQLA (High Quality Liquid Asset)",
        "governance_domain": "Fixed Income"
    }
)

# =================================================================
# SECTION B: Structural & Descriptive (The Blue Top Section)
# Maps to Schema Fields (The financial attributes of the bond)
# =================================================================

fields = [
    SchemaFieldClass(
        fieldPath="ISIN",
        type=SchemaFieldDataTypeClass(type=StringTypeClass()),
        description="International Securities Identification Number",
        nativeDataType="varchar(12)",
        globalTags=GlobalTagsClass(tags=[TagAssociationClass(tag=make_tag_urn("FIBO.SovereignDebt"))])
    ),
    SchemaFieldClass(
        fieldPath="CouponRate",
        type=SchemaFieldDataTypeClass(type=NumberTypeClass()),
        description="Annual interest rate paid on the bond",
        nativeDataType="decimal"
    ),
    SchemaFieldClass(
        fieldPath="MaturityDate",
        type=SchemaFieldDataTypeClass(type=DateTypeClass()),
        description="Date when the principal is repaid",
        nativeDataType="date"
    ),
    SchemaFieldClass(
        fieldPath="Issuer",
        type=SchemaFieldDataTypeClass(type=StringTypeClass()),
        description="Entity issuing the debt (e.g., US Government)",
        nativeDataType="varchar(50)"
    ),
]

schema_aspect = SchemaMetadataClass(
    schemaName="Bond Security Master",
    platform=make_data_platform_urn(platform),
    version=0,
    hash="",
    platformSchema=OtherSchemaClass(rawSchema="__empty__"),
    fields=fields,
)

# =================================================================
# SECTION C: Emit to DataHub
# =================================================================

aspects_to_emit = [ownership_aspect, properties_aspect, schema_aspect]

for aspect in aspects_to_emit:
    mcp = MetadataChangeProposalWrapper(
        entityUrn=dataset_urn,
        aspect=aspect
    )
    try:
        emitter.emit(mcp)
        print(f"Successfully emitted: {type(aspect).__name__}")
    except Exception as e:
        print(f"Failed to emit {type(aspect).__name__}: {e}")

print("Financial Metadata Tutorial Ingestion Complete.")
