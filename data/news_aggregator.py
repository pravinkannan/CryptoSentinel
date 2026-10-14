"""
NewsAggregatorService fetches RSS feeds from crypto sources.
"""

import feedparser
import aiohttp
import asyncio
import logging
from bs4 import BeautifulSoup
from typing import List, Dict, Set
from core.state_manager import StateManager

logger = logging.getLogger(__name__)

class NewsAggregatorService:
    """Aggregates RSS feeds and returns parsed recent news articles."""
    def __init__(self, rss_feeds: List[str], state_manager: StateManager):
        self.rss_feeds = rss_feeds
        self.state_manager = state_manager
        self.seen_guids: Set[str] = set(self.state_manager.state.get("seen_guids", []))

    def clean_html(self, raw_html: str) -> str:
        """Clean HTML tags from RSS summaries and normalize whitespace."""
        soup = BeautifulSoup(raw_html, "html.parser")
        text: str = str(soup.get_text(separator=" "))
        return " ".join(text.split())

    async def fetch_feed(self, session: aiohttp.ClientSession, url: str) -> List[Dict[str, str]]:
        """Fetch a single RSS feed."""
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                content = await response.text()
                parsed = feedparser.parse(content)
                articles = []
                for entry in parsed.entries[:10]:
                    guid = entry.get('id', entry.get('link'))
                    if guid and guid not in self.seen_guids:
                        articles.append({
                            'title': str(entry.get('title', '')),
                            'summary': self.clean_html(str(entry.get('summary', ''))),
                            'link': str(entry.get('link', '')),
                            'guid': str(guid)
                        })
                return articles
        except Exception as e:
            logger.error(f"Error fetching feed {url}: {e}")
            return []

    async def get_latest_news(self, limit: int = 5) -> List[Dict[str, str]]:
        """Fetch the latest unseen news from all configured RSS feeds."""
        all_articles = []
        async with aiohttp.ClientSession() as session:
            tasks = [self.fetch_feed(session, url) for url in self.rss_feeds]
            results = await asyncio.gather(*tasks)
            for res in results:
                all_articles.extend(res)
                
        new_articles = all_articles[:limit]
        for article in new_articles:
            if article.get('guid'):
                self.seen_guids.add(article['guid'])
            
        return new_articles
