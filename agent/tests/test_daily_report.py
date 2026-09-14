from agent.companion import ActionLabel, CompanionAction
from agent.holding_eval import HoldingEvaluation, HoldingVerdict
from agent.notify.daily_report import build_report_text
from agent.pipeline import PipelineResult
from agent.portfolio.store import Holding
from agent.translator.trade_plan import Side, TradePlan


def _flat_result(symbol: str, rating: str) -> PipelineResult:
    plan = TradePlan(
        symbol=symbol, side=Side.FLAT, entry=100.0, stop=100.0, target=100.0,
        risk_amount=0.0, suggested_qty=0, stop_distance=0.0, source_rating=rating,
    )
    return PipelineResult(
        symbol=symbol, analysis_date="2026-01-01", mode="deep", pm_decision_markdown="",
        trader_proposal_markdown="", market_report="", fundamentals_report="",
        trade_plan=plan, order_filled=False, account_report="", fills_report="",
        positions_report="",
    )


def _buy_result(symbol: str) -> PipelineResult:
    plan = TradePlan(
        symbol=symbol, side=Side.BUY, entry=1000.0, stop=960.0, target=1080.0,
        risk_amount=2500.0, suggested_qty=62, stop_distance=40.0, source_rating="Buy",
    )
    return PipelineResult(
        symbol=symbol, analysis_date="2026-01-01", mode="deep", pm_decision_markdown="",
        trader_proposal_markdown="", market_report="", fundamentals_report="",
        trade_plan=plan, order_filled=True, account_report="", fills_report="",
        positions_report="",
    )


def test_report_includes_needs_attention_section_for_exit():
    holding = Holding(symbol="RELIANCE", quantity=10, avg_price=1250.0)
    he = HoldingEvaluation(
        holding=holding, last_price=1200.0, unrealized_pnl=-500.0,
        unrealized_pnl_pct=-4.0, hard_stop=1208.0, hard_target=1330.0,
        verdict=HoldingVerdict.EXIT_STOP_HIT, ta_result=_flat_result("RELIANCE.NS", "Hold"),
    )
    action = CompanionAction(
        symbol="RELIANCE", label=ActionLabel.EXIT, is_existing_holding=True,
        screen_score=None, result=he.ta_result, holding_eval=he,
    )
    text = build_report_text([action], "2026-01-01")
    assert "Needs attention" in text
    assert "RELIANCE" in text
    assert "Exit" in text
    assert "-500" in text or "-₹500" in text


def test_report_includes_new_opportunities_section():
    result = _buy_result("TCS.NS")
    action = CompanionAction(
        symbol="TCS", label=ActionLabel.BUY_NEW, is_existing_holding=False,
        screen_score=45.2, result=result, holding_eval=None,
    )
    text = build_report_text([action], "2026-01-01")
    assert "New opportunities" in text
    assert "TCS" in text
    assert "entry" in text.lower()


def test_report_handles_empty_actions():
    text = build_report_text([], "2026-01-01")
    assert "Nothing actionable" in text
