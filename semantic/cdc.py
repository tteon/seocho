import json
import threading
import os
from kafka import KafkaConsumer
from neo4j_client import Neo4jClient

class DataHubCDC:
    def __init__(self):
        self.bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVER", "kafka:29092")
        self.neo4j_client = Neo4jClient()
        self.running = False
        self.thread = None

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._consume_loop)
        self.thread.daemon = True
        self.thread.start()
        print("DataHub CDC Consumer started.")

    def _consume_loop(self):
        # Retry connection logic would go here in prod
        try:
            consumer = KafkaConsumer(
                'MetadataChangeLog_v4',
                bootstrap_servers=self.bootstrap_servers,
                auto_offset_reset='latest',
                value_deserializer=lambda x: json.loads(x.decode('utf-8'))
            )
            
            for message in consumer:
                if not self.running:
                    break
                
                self._process_message(message.value)
        except Exception as e:
            print(f"CDC Consumer error: {e}")

    def _process_message(self, message):
        """
        Process DataHub Metadata Change Log (MCL) message.
        Extract relevant info and update Neo4j.
        """
        try:
            # Simplified logic to extract entity URN and type
            if 'entityUrn' in message:
                urn = message['entityUrn']
                # Example: urn:li:dataset:(urn:li:dataPlatform:hive,SampleHiveDataset,PROD)
                print(f"CDC received update for: {urn}")
                
                # Sync to Neo4j
                # We can store the URN as a node or update existing properties
                self.neo4j_client.merge_datahub_entity(urn)
                
        except Exception as e:
            print(f"Error processing CDC message: {e}")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()
