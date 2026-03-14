from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog

logger = structlog.get_logger()


class FinnhubProvider:
    """Fetch news headlines from Finnhub API.

    Free tier: 60 req/min. Returns headlines for a given symbol
    within the last 24 hours.
    """

    BASE_URL = 'https://finnhub.io/api/v1'

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._log = logger.bind(provider='finnhub')

    def get_news(
        self, symbol: str, hours: int = 24,
    ) -> list[dict]:
        """Fetch recent news headlines for a symbol.

        Returns list of dicts with keys: headline, source, datetime, url.
        """
        import requests

        now = datetime.now(timezone.utc)
        from_date = (now - timedelta(hours=hours)).strftime('%Y-%m-%d')
        to_date = now.strftime('%Y-%m-%d')

        try:
            resp = requests.get(
                f'{self.BASE_URL}/company-news',
                params={
                    'symbol': symbol,
                    'from': from_date,
                    'to': to_date,
                    'token': self._api_key,
                },
                timeout=10,
            )
            resp.raise_for_status()
            articles = resp.json()

            results = []
            for article in articles:
                results.append({
                    'headline': article.get('headline', ''),
                    'source': article.get('source', ''),
                    'datetime': article.get('datetime', 0),
                    'url': article.get('url', ''),
                    'summary': article.get('summary', ''),
                })

            self._log.info(
                'news_fetched',
                symbol=symbol,
                count=len(results),
            )
            return results

        except Exception as exc:
            self._log.error(
                'news_fetch_failed',
                symbol=symbol,
                error=str(exc),
            )
            return []
