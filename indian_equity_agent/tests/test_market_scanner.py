"""Unit tests for the High-Speed Whole-Market Stock Screener."""

import pytest
from indian_equity_agent.market_data.market_scanner import MarketScanner


def test_market_scanner_universe_coverage():
    scanner = MarketScanner()
    assert len(scanner.BROAD_NSE_UNIVERSE) >= 40
    # Verify both affordable (< ₹300) and large-cap stocks exist
    assert "TATASTEEL.NS" in scanner.BROAD_NSE_UNIVERSE
    assert "PNB.NS" in scanner.BROAD_NSE_UNIVERSE
    assert "RELIANCE.NS" in scanner.BROAD_NSE_UNIVERSE


def test_market_scanner_mock_ranking():
    scanner = MarketScanner()
    results = scanner.scan_market(top_n=5, use_mock=True)
    assert len(results) == 5
    for r in results:
        assert "symbol" in r
        assert "price" in r
        assert "score" in r
        assert r["price"] > 0
        assert r["score"] > 0

    # Verify results are sorted by score descending
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_market_scanner_capital_pre_filtering():
    scanner = MarketScanner()
    # If trader has ₹300 with 5x leverage = ₹1500 max buying power
    # Expensive stocks like TCS (₹4200) and RELIANCE (₹2900) must be excluded!
    results = scanner.scan_market(max_price_inr=500.0, top_n=10, use_mock=True)
    assert len(results) > 0
    for r in results:
        assert r["price"] <= 500.0
