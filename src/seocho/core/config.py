"""
Configuration management for Seocho framework
"""
import os
from typing import Dict, Any

class Config:
    """Configuration manager for Seocho"""
    
    # Neo4j/DozerDB settings
    NEO4J_URI = os.getenv('NEO4J_URI', 'bolt://localhost:7687')
    NEO4J_USER = os.getenv('NEO4J_USER', 'neo4j')
    NEO4J_PASSWORD = os.getenv('NEO4J_PASSWORD', 'thisisakccdemo')
    
    # DataHub settings
    DATAHUB_GMS_URL = os.getenv('DATAHUB_GMS_URL', 'http://localhost:8080')
    DATAHUB_TOKEN = os.getenv('DATAHUB_TOKEN', '')
    
    # File paths
    WORKSPACE_DIR = os.getenv('WORKSPACE_DIR', '/workspace')
    SHAREPOINT_DIR = os.getenv('SHAREPOINT_DIR', '/sharepoint')
    
    # Logging
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    
    @classmethod
    def get_config(cls) -> Dict[str, Any]:
        """Get all configuration as dictionary"""
        return {
            'neo4j': {
                'uri': cls.NEO4J_URI,
                'user': cls.NEO4J_USER,
                'password': cls.NEO4J_PASSWORD
            },
            'datahub': {
                'gms_url': cls.DATAHUB_GMS_URL,
                'token': cls.DATAHUB_TOKEN
            },
            'paths': {
                'workspace': cls.WORKSPACE_DIR,
                'sharepoint': cls.SHAREPOINT_DIR
            },
            'logging': {
                'level': cls.LOG_LEVEL
            }
        }