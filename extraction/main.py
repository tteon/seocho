import logging
import os

from pipeline import ExtractionPipeline
from config import configure_logging, load_pipeline_runtime_config


logger = logging.getLogger(__name__)


def main() -> None:
    configure_logging(os.getenv("LOG_LEVEL", "INFO"))
    cfg = load_pipeline_runtime_config()

    logger.info(
        "Starting extraction pipeline (mock_data=%s, model=%s, rules=%s)",
        cfg.mock_data,
        cfg.model,
        cfg.enable_rule_constraints,
    )
    pipeline = ExtractionPipeline(cfg)
    result = pipeline.run()
    if not result.success:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
