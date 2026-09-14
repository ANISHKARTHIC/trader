from agent.chat.tools import (
    TOOL_IMPLS,
    TOOL_SCHEMAS,
    register_start_analysis,
    tool_get_performance_summary,
    tool_get_portfolio,
    tool_start_analysis,
)


def test_every_schema_has_a_matching_implementation():
    schema_names = {s["function"]["name"] for s in TOOL_SCHEMAS}
    assert schema_names == set(TOOL_IMPLS.keys())


def test_get_portfolio_returns_holdings_list():
    result = tool_get_portfolio()
    assert "holdings" in result
    assert isinstance(result["holdings"], list)


def test_get_performance_summary_returns_dict():
    result = tool_get_performance_summary()
    assert "total_resolved" in result


def test_start_analysis_without_registered_callback_errors_gracefully():
    import agent.chat.tools as tools_module

    original = tools_module._start_analysis_callback
    tools_module._start_analysis_callback = None
    try:
        result = tool_start_analysis(symbol="RELIANCE", mode="quick")
        assert "error" in result
    finally:
        tools_module._start_analysis_callback = original


def test_start_analysis_calls_registered_callback_with_ns_suffix():
    calls = []

    def fake_callback(symbol_ns, mode):
        calls.append((symbol_ns, mode))
        return {"job_id": "abc123", "status": "running"}

    register_start_analysis(fake_callback)
    try:
        result = tool_start_analysis(symbol="reliance", mode="deep")
        assert calls == [("RELIANCE.NS", "deep")]
        assert result["job_id"] == "abc123"
    finally:
        register_start_analysis(None)


def test_start_analysis_defaults_invalid_mode_to_quick():
    calls = []
    register_start_analysis(lambda s, m: calls.append((s, m)) or {"job_id": "x"})
    try:
        tool_start_analysis(symbol="TCS", mode="bogus")
        assert calls == [("TCS.NS", "quick")]
    finally:
        register_start_analysis(None)
