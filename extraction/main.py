import hydra
from omegaconf import DictConfig, OmegaConf
from pipeline import ExtractionPipeline
import os

@hydra.main(version_base=None, config_path="conf", config_name="config")
def main(cfg: DictConfig):
    print(OmegaConf.to_yaml(cfg))
    
    # Initialize and run pipeline
    pipeline = ExtractionPipeline(cfg)
    pipeline.run()

if __name__ == "__main__":
    main()
