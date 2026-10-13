"""Additional checks; the instructor's tests remain unchanged."""

from gameday_lab.cache import SemanticCache
from gameday_lab.client import ResilientClient
from gameday_lab.sim.contracts import Answer, Attempt, CallContext, GiveUp, ProviderError, ProviderInfo, Request, Send
from gameday_lab.slo.alerting import BurnRateAlerter, error_budget_report
from gameday_lab.workflows.executor import WorkflowExecutor, checkpoint_key
from gameday_lab.workflows.runner import RefundRequest
from gameday_lab.workflows.services import DurableStore, FaultProfile, Services


def test_rate_limit_wait_cannot_bypass_empty_retry_budget() -> None:
    providers = {"only": ProviderInfo("only", "A", "us", True, 1.0)}
    client = ResilientClient(providers, ["only"], {
        "retry_budget_ratio": 0, "retry_budget_max_tokens": 0, "retry_budget_min_per_s": 0})
    call = CallContext(1, 1, "tool", 0, 30)
    send = client.start(call, 0)
    assert isinstance(send, Send)
    action = client.on_error(Attempt(call, "only", 1, 0, send.tag),
                             ProviderError("only", 429, "rate_limit_exceeded", retry_after=1), 0.1)
    assert isinstance(action, GiveUp)


def test_cache_rejects_answer_from_another_prompt_scope() -> None:
    cache = SemanticCache()
    req = Request("FAQ", "u1", False, "v2", (1.0, 0.0))
    cache.store(req, Answer(1, "v1", ("source",), 0), 0)
    assert cache.lookup(req, 1) is None
    assert not cache.entries


def test_cache_does_not_compare_different_embedding_dimensions() -> None:
    cache = SemanticCache()
    cache.store(Request("FAQ", "u1", False, "v1", (1.0,)), Answer(1, "v1", (), 0), 0)
    assert cache.lookup(Request("FAQ", "u1", False, "v1", (1.0, 0.0)), 1) is None


def test_empty_slo_data_neither_pages_nor_freezes_releases() -> None:
    assert BurnRateAlerter().evaluate([], []) == []
    report = error_budget_report([], [])
    assert report["budget_consumed"] == 0
    assert report["freeze_releases"] is False


def test_checkpoint_with_null_result_is_still_complete() -> None:
    services = Services.build(1, FaultProfile(0, 0, 0))
    store = DurableStore()
    request = RefundRequest("session", "order", 4900, "customer@example.com")
    WorkflowExecutor(services, store).run(request)
    store.put(checkpoint_key(request, "write_summary"), None)
    llm_calls = services.llm.executions
    result = WorkflowExecutor(services, store).run(request)
    assert result["write_summary"] is None
    assert services.llm.executions == llm_calls
