import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["TITAN_LLM"] = "rules"          # deterministic: never call the API from tests
os.environ.pop("ANTHROPIC_API_KEY", None)

from titan import config as config_mod, db  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture
def cfg():
    return config_mod.load(ROOT / "config.yaml", env_path=FIXTURES / "nonexistent.env", force=True)


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture
def loaded(conn, cfg):
    """DB with the fixture listings ingested, scored, valued, ranked."""
    from titan.ingest.pipeline import ingest
    from titan.scoring import run_scoring
    from titan.valuation import run_valuation
    import json
    ingest(conn, cfg, fixture=FIXTURES / "listings_sample.json")
    run_scoring(conn, cfg, llm=None)
    comps = json.loads((FIXTURES / "sold_comps_sample.json").read_text())
    run_valuation(conn, cfg, comps_fn=lambda p: comps)
    return conn
