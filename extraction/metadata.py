import os
import time
from datahub.emitter.mce_builder import make_dataset_urn, make_data_platform_urn
from datahub.emitter.rest_emitter import DatahubRestEmitter
from datahub.metadata.schema_classes import DatasetPropertiesClass, UpstreamLineageClass

class MetadataHandler:
    def __init__(self):
        self.gms_url = os.getenv("DATAHUB_GMS_URL", "http://datahub-gms:8080")
        # In a real scenario, we would handle connection errors gracefully
        print(f"Initializing DataHub emitter with URL: {self.gms_url}")
        self.emitter = DatahubRestEmitter(gms_server=self.gms_url)

    def emit_metadata(self, data_item: dict):
        """
        Emits metadata to DataHub for a given data item.
        """
        dataset_urn = make_dataset_urn(platform="custom", name=data_item["id"], env="PROD")
        
        properties = DatasetPropertiesClass(
            description=f"Raw data from {data_item['source']}",
            customProperties={
                "source": data_item["source"],
                "content_length": str(len(data_item["content"]))
            }
        )

        try:
            # Emit dataset properties
            self.emitter.emit_mcp(
                entity_type="dataset",
                entity_urn=dataset_urn,
                aspect_name="datasetProperties",
                aspect=properties
            )
            print(f"Emitted metadata for {data_item['id']}")
        except Exception as e:
            print(f"Failed to emit metadata for {data_item['id']}: {e}")
