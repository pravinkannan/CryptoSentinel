"""
Enterprise unit tests for NewsAggregatorService.
Tests:
- HTML cleaning and tag sanitization
- RSS XML feed parsing (title, summary, link, guid fallback)
- GUID deduplication across cycles and multiple feeds
- Resilience to network timeouts, connection errors, and malformed XML
- Feed limit bounds
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from data.news_aggregator import NewsAggregatorService
from core.state_manager import StateManager


SAMPLE_RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Crypto News Test Feed</title>
    <link>https://example.com/feed</link>
    <description>Sample crypto news for unit testing</description>
    <item>
      <title>Bitcoin Breaches Key Level</title>
      <link>https://example.com/btc-news-1</link>
      <guid>btc-guid-001</guid>
      <description>&lt;p&gt;Bitcoin has rallied above the &lt;strong&gt;$65,000&lt;/strong&gt; barrier today.&lt;/p&gt;</description>
    </item>
    <item>
      <title>Ethereum Layer 2 Upgrades</title>
      <link>https://example.com/eth-news-2</link>
      <guid>eth-guid-002</guid>
      <description>&lt;div&gt;Gas fees drop significantly following &lt;span&gt;network update&lt;/span&gt;.&lt;/div&gt;</description>
    </item>
    <item>
      <title>Solana Ecosystem Growth</title>
      <link>https://example.com/sol-news-3</link>
      <!-- Note: Missing guid tag to test link fallback -->
      <description>Ecosystem volume reaches record monthly highs.</description>
    </item>
  </channel>
</rss>
"""


@pytest.fixture
def news_aggregator(isolated_state_manager):
    """Creates a NewsAggregatorService with isolated StateManager."""
    return NewsAggregatorService(
        rss_feeds=["https://example.com/feed1", "https://example.com/feed2"],
        state_manager=isolated_state_manager
    )


# =========================================================================
# 1. HTML Sanitization Tests
# =========================================================================

def test_clean_html_strips_nested_tags_and_entities(news_aggregator):
    """
    Positive case: Verifies that HTML markup (tags, entities, linebreaks)
    is cleanly converted to plain readable text.
    """
    raw_html = "<p>Breaking: <strong>Bitcoin</strong> reaches <em>$70k</em>. Visit <a href='https://link'>here</a>!</p>"
    cleaned = news_aggregator.clean_html(raw_html)
    assert cleaned == "Breaking: Bitcoin reaches $70k . Visit here !"
    assert "<" not in cleaned and ">" not in cleaned


def test_clean_html_plain_text_passthrough(news_aggregator):
    """
    Negative/Edge case: Plain text without markup is preserved without modification.
    """
    plain = "Federal Reserve announces interest rate decision unchanged."
    assert news_aggregator.clean_html(plain) == plain


def test_clean_html_empty_and_whitespace_edge_cases(news_aggregator):
    """
    Edge case: Empty string and whitespace strings return empty cleanly.
    """
    assert news_aggregator.clean_html("") == ""
    assert news_aggregator.clean_html("   \n\t  ") == ""


# =========================================================================
# 2. Feed Parsing & GUID Fallback Tests
# =========================================================================

def test_fetch_feed_parsing_and_fallback(news_aggregator):
    """
    Positive case: Parses XML feed into structured articles.
    Verifies link fallback when guid tag is omitted.
    """
    async def run_test():
        mock_response = AsyncMock()
        mock_response.text = AsyncMock(return_value=SAMPLE_RSS_XML)

        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__.return_value = mock_response

        articles = await news_aggregator.fetch_feed(mock_session, "https://example.com/feed")
        
        assert len(articles) == 3
        # First article: guid is explicit
        assert articles[0]['title'] == "Bitcoin Breaches Key Level"
        assert articles[0]['guid'] == "btc-guid-001"
        assert "Bitcoin has rallied above the $65,000 barrier today." in articles[0]['summary']

        # Third article: guid falls back to link
        assert articles[2]['guid'] == "https://example.com/sol-news-3"

    asyncio.run(run_test())


def test_fetch_feed_deduplication(news_aggregator):
    """
    Positive case: Articles whose GUID already exists in seen_guids are filtered out.
    """
    async def run_test():
        # Pre-seed seen_guids with article 1
        news_aggregator.seen_guids.add("btc-guid-001")

        mock_response = AsyncMock()
        mock_response.text = AsyncMock(return_value=SAMPLE_RSS_XML)
        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__.return_value = mock_response

        articles = await news_aggregator.fetch_feed(mock_session, "https://example.com/feed")
        
        # Only articles 2 and 3 should be returned
        assert len(articles) == 2
        guids = [a['guid'] for a in articles]
        assert "btc-guid-001" not in guids
        assert "eth-guid-002" in guids

    asyncio.run(run_test())


# =========================================================================
# 3. Network Resilience & Error Handling Tests
# =========================================================================

def test_fetch_feed_network_timeout_resilience(news_aggregator):
    """
    Negative case: Feed request times out or raises an exception.
    Should catch the error, log it, and safely return an empty list without crashing.
    """
    async def run_test():
        mock_session = MagicMock()
        mock_session.get.side_effect = asyncio.TimeoutError("Connection timed out after 10s")

        articles = await news_aggregator.fetch_feed(mock_session, "https://example.com/dead-feed")
        assert articles == []

    asyncio.run(run_test())


def test_fetch_feed_malformed_xml_resilience(news_aggregator):
    """
    Edge case: Server returns non-XML garbage (e.g. 502 Bad Gateway HTML).
    """
    async def run_test():
        mock_response = AsyncMock()
        mock_response.text = AsyncMock(return_value="<html><body>502 Bad Gateway</body></html>")
        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__.return_value = mock_response

        articles = await news_aggregator.fetch_feed(mock_session, "https://example.com/feed")
        # Should return empty list cleanly without crashing
        assert isinstance(articles, list)

    asyncio.run(run_test())


# =========================================================================
# 4. Multi-Feed Aggregation & Limit Bounds
# =========================================================================

def test_get_latest_news_aggregation_and_limit(news_aggregator):
    """
    Verifies multi-feed gathering, limit bounding, and persistent addition to seen_guids.
    """
    async def run_test():
        # Mock fetch_feed to return 3 articles per feed (6 total)
        feed_articles = [
            {'title': f'News {i}', 'summary': f'Summary {i}', 'link': f'http://{i}', 'guid': f'g_{i}'}
            for i in range(6)
        ]
        news_aggregator.fetch_feed = AsyncMock(side_effect=[feed_articles[:3], feed_articles[3:]])

        # Request with limit=4
        results = await news_aggregator.get_latest_news(limit=4)

        assert len(results) == 4
        # The 4 returned articles must have their GUIDs added to seen_guids
        for r in results:
            assert r['guid'] in news_aggregator.seen_guids

    asyncio.run(run_test())
