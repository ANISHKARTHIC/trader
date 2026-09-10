from agent.translator.from_tradingagents import (
    _extract_price_field,
    _PRICE_FIELD_RE,
    _STOP_FIELD_RE,
    build_trade_plan_inputs_from_state,
)
from agent.translator.trade_plan import Side, build_trade_plan


def test_hold_from_real_saved_state():
    # Same rendered-markdown shape TradingAgents actually writes (see
    # render_pm_decision / render_trader_proposal in schemas.py).
    state = {
        "company_of_interest": "RELIANCE.NS",
        "final_trade_decision": (
            "**Rating**: Hold\n\n**Executive Summary**: Maintain exposure."
        ),
        "trader_investment_plan": "**Action**: Hold\n\n**Reasoning**: Mixed signals.",
    }
    inputs = build_trade_plan_inputs_from_state(state, account_equity=500_000.0)
    assert inputs.rating == "Hold"
    plan = build_trade_plan(inputs)
    assert plan.side is Side.FLAT
    assert plan.suggested_qty == 0


def test_extracts_llm_entry_and_stop_from_trader_proposal_markdown():
    trader_text = (
        "**Action**: Buy\n\n"
        "**Reasoning**: Strong breakout.\n\n"
        "**Entry Price**: 1,280.50\n\n"
        "**Stop Loss**: 1,250.0\n\n"
        "FINAL TRANSACTION PROPOSAL: **BUY**"
    )
    entry = _extract_price_field(_PRICE_FIELD_RE, trader_text)
    stop = _extract_price_field(_STOP_FIELD_RE, trader_text)
    assert entry == 1280.50
    assert stop == 1250.0


def test_buy_rating_flows_to_nonzero_trade_plan():
    state = {
        "company_of_interest": "RELIANCE.NS",
        "final_trade_decision": "**Rating**: Buy\n\n**Executive Summary**: Enter now.",
        "trader_investment_plan": "**Action**: Buy\n\n**Reasoning**: Momentum.",
    }
    inputs = build_trade_plan_inputs_from_state(state, account_equity=500_000.0)
    assert inputs.rating == "Buy"
    plan = build_trade_plan(inputs)
    assert plan.side is Side.BUY
    assert plan.suggested_qty > 0
