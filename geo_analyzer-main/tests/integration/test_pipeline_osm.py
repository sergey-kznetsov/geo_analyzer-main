from pathlib import Path


def test_project_tree_has_main_files():
    base = Path(__file__).resolve().parents[2]
    assert (base / "src" / "geo_analyzer" / "pipeline" / "analysis_pipeline.py").exists()
    assert (base / "config" / "config.yaml").exists()
    assert (base / "scripts" / "run_analysis.py").exists()
