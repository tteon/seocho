import time
import requests
import os
from collector import DataCollector
from metadata import MetadataHandler

def send_to_semantic_layer(data):
    """
    Sends collected data to the Semantic Layer.
    In a real architecture, this might be via a message queue (Kafka/RabbitMQ).
    For this POC, we'll assume a direct HTTP call or just processing it here if we merge services,
    but let's simulate sending it to the semantic service API.
    """
    semantic_service_url = os.getenv("SEMANTIC_SERVICE_URL", "http://semantic-service:8000/ingest")
    try:
        response = requests.post(semantic_service_url, json=data)
        if response.status_code == 200:
            print(f"Successfully sent {len(data)} items to Semantic Layer")
        else:
            print(f"Failed to send data: {response.status_code}")
    except Exception as e:
        print(f"Error sending to Semantic Layer: {e}")

def main():
    print("Starting Extraction Service...")
    collector = DataCollector()
    metadata_handler = MetadataHandler()

    while True:
        # 1. Collect Data
        raw_data = collector.collect_raw_data()
        
        # 2. Process Metadata & Send to DataHub
        for item in raw_data:
            metadata_handler.emit_metadata(item)
        
        # 3. Send to Semantic Layer
        # For simplicity in this POC, we are just printing. 
        # To make it "straight" and working, we will simulate the handoff.
        # Since we haven't built the semantic service API yet, this call would fail.
        # We will implement the semantic service next.
        send_to_semantic_layer(raw_data)

        print("Cycle complete. Sleeping...")
        time.sleep(60) # Run every minute

if __name__ == "__main__":
    main()
