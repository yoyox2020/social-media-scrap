"""Unit tests untuk PlannerAgent (rule-based mode)."""
import pytest

from unittest.mock import AsyncMock, MagicMock


def _make_planner():
    from app.services.agents.planner import PlannerAgent
    db = MagicMock()
    return PlannerAgent(db)


def test_plan_sentiment_question():
    planner = _make_planner()
    plan = planner._plan_with_rules("Apa sentimen terkait produk ini?")
    assert "sentiment" in plan
    assert "summary" in plan
    assert plan[-1] == "summary"


def test_plan_entity_question():
    planner = _make_planner()
    plan = planner._plan_with_rules("Siapa tokoh yang paling sering disebutkan?")
    assert "entity" in plan
    assert plan[-1] == "summary"


def test_plan_trend_question():
    planner = _make_planner()
    plan = planner._plan_with_rules("Bagaimana tren volume post minggu ini?")
    assert "trend" in plan
    assert plan[-1] == "summary"


def test_plan_search_question():
    planner = _make_planner()
    plan = planner._plan_with_rules("Cari post yang membahas harga naik")
    assert "search" in plan
    assert plan[-1] == "summary"


def test_plan_unknown_question_defaults():
    planner = _make_planner()
    plan = planner._plan_with_rules("halo")
    # Default plan includes sentiment, entity, trend
    assert len(plan) >= 2
    assert "summary" in plan
    assert plan[-1] == "summary"


def test_plan_no_duplicate_summary():
    planner = _make_planner()
    # Pertanyaan yang mengandung beberapa keyword
    plan = planner._plan_with_rules("Sentimen dan tren dari tokoh ini")
    assert plan.count("summary") == 1


def test_plan_order_summary_last():
    planner = _make_planner()
    plan = planner._plan_with_rules("Cari dan analisis sentimen tren")
    assert plan[-1] == "summary"
    # Agents lain harus sebelum summary
    non_summary = [a for a in plan if a != "summary"]
    assert len(non_summary) >= 1
