from __future__ import annotations

from pipeline.schemas import Finding
from pipeline.synthesis.section_synthesis import synthesize_company_section
from tests.pipeline.conftest import FakeChatModel


def test_synthesize_company_section_returns_llm_content():
    llm = FakeChatModel(text_response="NVDA had a strong week.")
    findings = [
        Finding(
            scope="company",
            ticker="NVDA",
            event_title="Chip launch",
            event_type="product",
            narrative="NVDA launched a new chip.",
            source_urls=["https://reuters.com/nvda"],
        )
    ]

    result = synthesize_company_section(
        "NVDA",
        findings=findings,
        scores={"NVDA": {"composite_score": 87.5}},
        financials={"NVDA": {"forward_pe": 34.2}},
        llm=llm,
    )

    assert result == "NVDA had a strong week."


def test_synthesize_company_section_filters_findings_to_this_ticker_only():
    llm = FakeChatModel(text_response="ok")
    findings = [
        Finding(
            scope="company", ticker="NVDA", event_title="a", event_type="product", narrative="n"
        ),
        Finding(
            scope="company", ticker="MSFT", event_title="b", event_type="product", narrative="m"
        ),
        Finding(scope="macro", topic="Fed policy", event_title="c", event_type="macro", narrative="f"),
    ]

    synthesize_company_section("NVDA", findings=findings, scores={}, financials={}, llm=llm)

    prompt = llm.invocations[0][1][1]  # (system, user) messages, user text
    assert "n" in prompt or "NVDA" in prompt
    assert "MSFT" not in prompt
    assert "Fed policy" not in prompt


def test_synthesize_company_section_handles_missing_data_gracefully():
    llm = FakeChatModel(text_response="ok")
    result = synthesize_company_section(
        "GAP", findings=[], scores={}, financials={}, llm=llm
    )
    assert result == "ok"
