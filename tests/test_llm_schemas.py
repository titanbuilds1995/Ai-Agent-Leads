"""Guards for the Claude calls: every schema must be accepted by structured outputs, and a failing Claude must be loud."""
from titan.outreach.compose import EMAIL_SCHEMA
from titan.replies.classifier import LLM_SCHEMA
from titan.scoring import SCORE_SCHEMA, run_scoring

# structured outputs reject these with a 400 (raw messages.create does not strip them)
UNSUPPORTED = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minLength", "maxLength",
               "minItems", "maxItems", "uniqueItems", "pattern"}


def _walk(node, path="$"):
    if isinstance(node, dict):
        for k, v in node.items():
            assert k not in UNSUPPORTED, f"{path}.{k} is not supported by structured outputs"
            if k == "type" and v == "object":
                assert node.get("additionalProperties") is False, f"{path} needs additionalProperties: false"
            _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk(v, f"{path}[{i}]")


def test_every_schema_is_accepted_by_structured_outputs():
    for s in (SCORE_SCHEMA, EMAIL_SCHEMA, LLM_SCHEMA):
        _walk(s)


class FailingLLM:
    """Claude 'available' but every call errors, like the 3,256-call run on 2026-10-23."""
    model, provider = "claude-sonnet-5-5", "claude"

    def __init__(self, cfg=None):
        self.cfg = cfg
        self.session_cost, self.session_failures, self.session_successes, self.last_error, self.calls = 0.0, 0, 0, None, 0

    @property
    def available(self):
        return self.provider == "claude"

    def structured(self, *a, **k):
        self.calls += 1
        self.session_failures += 1
        self.last_error = "BadRequestError: schema rejected"
        return None


def test_failing_claude_switches_off_fast_and_warns(loaded, cfg):
    llm = FailingLLM(cfg)
    out = run_scoring(loaded, cfg, llm, force=True)
    assert llm.calls == 5 and out["claude_failed"] == 5 and "switched off" in out["warning"] and out["claude"] == 0
    assert out["rules"] == out["scored"] > 5


def test_rules_scores_are_redone_once_claude_works(loaded, cfg):
    run_scoring(loaded, cfg, None, force=True)                       # everything scored by rules

    class OkLLM(FailingLLM):
        def structured(self, *a, **k):
            self.calls += 1
            self.session_successes += 1
            return {"score": 150, "cited": [{"signal": "needs_work", "phrase": "needs work"}], "negatives": [], "summary": "s"}
    llm = OkLLM(cfg)
    out = run_scoring(loaded, cfg, llm)                               # no --force needed
    assert out["claude"] == out["scored"] > 0 and llm.calls == out["scored"]
    assert loaded.execute("SELECT MAX(score) FROM scores").fetchone()[0] == 100   # clamped locally
