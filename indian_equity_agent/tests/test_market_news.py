"""Tests for MarketNewsEngine."""

from indian_equity_agent.market_data.market_news import MarketNewsEngine


def test_market_news_engine():
    mne = MarketNewsEngine(cache_ttl_seconds=60)
    data = mne.fetch_market_news()
    assert "sentiment" in data
    assert data["sentiment"] in ("BULLISH", "BEARISH", "NEUTRAL")
    assert "score" in data
    assert -1.0 <= data["score"] <= 1.0
    assert "top_headlines" in data
    assert isinstance(data["top_headlines"], list)

    # Test symbol news lookup (< 1ms cached)
    sym_news = mne.get_symbol_news("TATASTEEL.NS")
    assert "symbol" in sym_news
    assert "market_sentiment" in sym_news
    assert sym_news["market_sentiment"] == data["sentiment"]
