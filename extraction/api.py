from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
import hydra
from omegaconf import DictConfig, OmegaConf
from pipeline import ExtractionPipeline
import os
import threading

app = FastAPI(title="SEOCHO Extraction API", version="1.0.0")

# Global pipeline instance
pipeline_instance = None
pipeline_lock = threading.Lock()

class PipelineConfig(BaseModel):
    mock_data: bool = True
    model: str = "gpt-3.5-turbo"

@app.on_event("startup")
async def startup_event():
    """
    Initialize the pipeline functionality. 
    We delay full initialization until the first run or keep it lightweight 
    if there are resources to save.
    """
    # Load default config
    with hydra.initialize(version_base=None, config_path="conf"):
        global default_cfg
        default_cfg = hydra.compose(config_name="config")
    print("API Startup: Hydra config loaded.")

@app.get("/")
def read_root():
    return {"status": "Extraction Service is running"}

@app.post("/run")
async def run_extraction(config: PipelineConfig, background_tasks: BackgroundTasks):
    """
    Triggers the extraction pipeline.
    """
    if pipeline_lock.locked():
        raise HTTPException(status_code=409, detail="Pipeline is already running.")
    
    background_tasks.add_task(execute_pipeline, config)
    return {"status": "Pipeline started", "config": config}

def execute_pipeline(config: PipelineConfig):
    with pipeline_lock:
        print(f"Starting pipeline with config: {config}")
        
        # Override hydra config with API params
        # Note: This is a simplified override. For full hydra support, 
        # we might want to reload or use overrides list.
        # But here we just modify the DictConfig object if possible or reload.
        
        # Reload config with overrides
        global default_cfg
        # We can just update the keys we care about for the instance
        cfg = default_cfg.copy()
        cfg.mock_data = config.mock_data
        cfg.model = config.model
        
        try:
            pipeline = ExtractionPipeline(cfg)
            pipeline.run()
            print("Pipeline finished successfully.")
        except Exception as e:
            print(f"Pipeline failed: {e}")

@app.get("/status")
def get_status():
    return {
        "running": pipeline_lock.locked()
    }
