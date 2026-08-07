import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("sdcr_text_chunk_smoke", Path(__file__).with_name("sdcr_text_chunk_smoke.py"))
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
run = module.run


def test_text_chunk_smoke_selects_two_isolated_views() -> None:
    result = run("Acme reported revenue and is involved in a patent dispute.")
    assert result["sdcr_receipt"]["selected_views"] == ["financials", "legal"]
    assert result["sdcr_receipt"]["workspace_id"] == "smoke-workspace"
    assert result["conflict_verification"]["status"] == "consistent"
