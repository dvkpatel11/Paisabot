from __future__ import annotations

import structlog

logger = structlog.get_logger()


class RedditProvider:
    """Fetch mentions and sentiment from Reddit using PRAW.

    Monitors r/wallstreetbets, r/investing, r/stocks.
    """

    SUBREDDITS = ['wallstreetbets', 'investing', 'stocks']

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        user_agent: str = 'paisabot:v1.0 (by /u/paisabot)',
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._user_agent = user_agent
        self._reddit = None
        self._log = logger.bind(provider='reddit')

    def _get_client(self):
        """Lazy-init PRAW client."""
        if self._reddit is None:
            import praw
            self._reddit = praw.Reddit(
                client_id=self._client_id,
                client_secret=self._client_secret,
                user_agent=self._user_agent,
            )
        return self._reddit

    def get_mentions(
        self, symbol: str, limit: int = 100,
    ) -> list[dict]:
        """Search for symbol mentions across monitored subreddits.

        Returns list of dicts with keys: title, text, score, created_utc, subreddit.
        """
        try:
            reddit = self._get_client()
            results = []

            for sub_name in self.SUBREDDITS:
                try:
                    subreddit = reddit.subreddit(sub_name)
                    for submission in subreddit.search(
                        symbol, sort='new', time_filter='week', limit=limit,
                    ):
                        results.append({
                            'title': submission.title,
                            'text': submission.selftext[:500] if submission.selftext else '',
                            'score': submission.score,
                            'created_utc': submission.created_utc,
                            'subreddit': sub_name,
                        })
                except Exception as exc:
                    self._log.warning(
                        'subreddit_search_failed',
                        subreddit=sub_name,
                        error=str(exc),
                    )

            self._log.info(
                'reddit_mentions_fetched',
                symbol=symbol,
                count=len(results),
            )
            return results

        except Exception as exc:
            self._log.error(
                'reddit_fetch_failed',
                symbol=symbol,
                error=str(exc),
            )
            return []

    def get_sentiment_counts(
        self, symbol: str, limit: int = 100,
    ) -> dict:
        """Get bull/bear/neutral mention counts for a symbol.

        Uses VADER sentiment on titles + text.
        Returns {bull: int, bear: int, neutral: int, total: int}.
        """
        mentions = self.get_mentions(symbol, limit=limit)
        if not mentions:
            return {'bull': 0, 'bear': 0, 'neutral': 0, 'total': 0}

        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            analyzer = SentimentIntensityAnalyzer()
        except ImportError:
            self._log.warning('vader_not_available')
            return {
                'bull': 0, 'bear': 0,
                'neutral': len(mentions), 'total': len(mentions),
            }

        bull = bear = neutral = 0
        for mention in mentions:
            text = f"{mention['title']} {mention['text']}"
            scores = analyzer.polarity_scores(text)
            compound = scores['compound']

            if compound >= 0.05:
                bull += 1
            elif compound <= -0.05:
                bear += 1
            else:
                neutral += 1

        return {
            'bull': bull,
            'bear': bear,
            'neutral': neutral,
            'total': bull + bear + neutral,
        }
