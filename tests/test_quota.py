from core.quota import format_tavily_usage


def test_format_tavily_usage_includes_remaining_credits():
    text = format_tavily_usage({"key": {"usage": 125, "limit": 1000}})
    assert "125 / 1.000" in text
    assert "875" in text


def test_format_tavily_usage_handles_unavailable_data():
    assert "belum tersedia" in format_tavily_usage({})
