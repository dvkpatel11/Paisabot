from __future__ import annotations

import numpy as np
import structlog

from app.factors.base import FactorBase

logger = structlog.get_logger()

# Module-level FinBERT singleton — loaded once on first use, reused thereafter.
# Loading the ~100 MB model on every call blocks the worker for 10-30 seconds.
_finbert_tokenizer = None
_finbert_model = None


def _get_finbert():
    """Return the (tokenizer, model) singleton, loading on first call."""
    global _finbert_tokenizer, _finbert_model
    if _finbert_tokenizer is None or _finbert_model is None:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        model_name = 'ProsusAI/finbert'
        logger.info('finbert_loading', model=model_name)
        _finbert_tokenizer = AutoTokenizer.from_pretrained(model_name)
        _finbert_model = AutoModelForSequenceClassification.from_pretrained(model_name)
        _finbert_model.eval()
        logger.info('finbert_loaded')
    return _finbert_tokenizer, _finbert_model


class SentimentFactor(FactorBase):
    """F03 — Sentiment Score (weight: 0.15).

    sentiment_score = 0.35 * news_score
                    + 0.25 * reddit_score
                    + 0.25 * options_score
                    + 0.15 * flow_score

    Degradation: missing components get 0.5 (neutral) and weights are
    redistributed proportionally among available components.
    """

    name = 'sentiment_score'
    weight = 0.15
    update_frequency = 'intraday'

    COMPONENT_WEIGHTS = {
        'news': 0.35,
        'reddit': 0.25,
        'options': 0.25,
        'flow': 0.15,
    }

    def compute(self, symbols: list[str]) -> dict[str, float]:
        results = {}
        for symbol in symbols:
            components = {}
            available_weight = 0.0

            # 1. News sentiment (FinBERT)
            news_score = self._compute_news_score(symbol)
            if news_score is not None:
                components['news'] = news_score
                available_weight += self.COMPONENT_WEIGHTS['news']

            # 2. Reddit sentiment (VADER)
            reddit_score = self._compute_reddit_score(symbol)
            if reddit_score is not None:
                components['reddit'] = reddit_score
                available_weight += self.COMPONENT_WEIGHTS['reddit']

            # 3. Put/Call ratio (options)
            options_score = self._compute_options_score(symbol)
            if options_score is not None:
                components['options'] = options_score
                available_weight += self.COMPONENT_WEIGHTS['options']

            # 4. Fund flow
            flow_score = self._compute_flow_score(symbol)
            if flow_score is not None:
                components['flow'] = flow_score
                available_weight += self.COMPONENT_WEIGHTS['flow']

            # Combine with weight redistribution
            if available_weight > 0:
                score = sum(
                    self.COMPONENT_WEIGHTS[name] / available_weight * value
                    for name, value in components.items()
                )
            else:
                score = 0.5  # fully neutral when no data

            results[symbol] = max(0.0, min(1.0, score))

        return results

    def _compute_news_score(self, symbol: str) -> float | None:
        """FinBERT sentiment on recent news headlines.

        Batches headlines (batch_size=32) for efficiency.
        Returns None if insufficient data (<5 headlines).
        """
        headlines = self._get_news_headlines(symbol)
        if len(headlines) < 5:
            return None

        try:
            scores = self._run_finbert(headlines)
            if not scores:
                return None
            return float(np.mean(scores))
        except Exception as exc:
            self._log.warning(
                'finbert_failed', symbol=symbol, error=str(exc),
            )
            # Fallback to VADER for news
            return self._vader_score(headlines)

    def _compute_reddit_score(self, symbol: str) -> float | None:
        """Reddit bull/bear ratio normalized to [0, 1]."""
        counts = self._get_reddit_counts(symbol)
        if counts is None or counts.get('total', 0) < 3:
            return None

        total = counts['total']
        if total == 0:
            return None

        # (bull - bear) / total → range [-1, 1] → normalize to [0, 1]
        ratio = (counts['bull'] - counts['bear']) / total
        return (ratio + 1.0) / 2.0

    def _compute_options_score(self, symbol: str) -> float | None:
        """Put/Call ratio score.

        1 - percentile_rank(PC_ratio_10d_MA, 252d_history).
        Returns None if data unavailable.
        """
        # Check Redis for cached P/C ratio
        if self._redis is None:
            return None

        try:
            pc_raw = self._redis.get(f'options:{symbol}:pc_ratio')
            if pc_raw is None:
                return None

            pc_ratio = float(pc_raw)

            # Get history for percentile ranking
            history_raw = self._redis.get(f'options:{symbol}:pc_history')
            if history_raw is None:
                return None

            import json
            history = [float(v) for v in json.loads(history_raw)]
            if len(history) < 30:
                return None

            from app.utils.normalization import percentile_rank
            pct = percentile_rank(pc_ratio, history)
            return 1.0 - pct  # Inverted: low P/C = bullish = high score

        except (ValueError, TypeError):
            return None

    def _compute_flow_score(self, symbol: str) -> float | None:
        """Fund flow percentile score.

        percentile_rank(net_flow_5d, 252d_history).
        Returns None if data unavailable.
        """
        if self._redis is None:
            return None

        try:
            flow_raw = self._redis.get(f'flow:{symbol}:net_5d')
            if flow_raw is None:
                return None

            flow = float(flow_raw)

            history_raw = self._redis.get(f'flow:{symbol}:history')
            if history_raw is None:
                return None

            import json
            history = [float(v) for v in json.loads(history_raw)]
            if len(history) < 30:
                return None

            from app.utils.normalization import percentile_rank
            return percentile_rank(flow, history)

        except (ValueError, TypeError):
            return None

    def _get_news_headlines(self, symbol: str) -> list[str]:
        """Fetch news headlines from cached data or provider."""
        # Try Redis cache first
        if self._redis is not None:
            import json
            cached = self._redis.get(f'news:{symbol}:headlines')
            if cached:
                try:
                    return json.loads(cached)
                except (ValueError, TypeError):
                    pass

        # Fallback: try Finnhub provider
        try:
            import os
            api_key = os.environ.get('FINNHUB_API_KEY', '')
            if not api_key:
                return []
            from app.data.finnhub_provider import FinnhubProvider
            provider = FinnhubProvider(api_key)
            articles = provider.get_news(symbol, hours=24)
            return [a['headline'] for a in articles if a.get('headline')]
        except Exception:
            return []

    def _get_reddit_counts(self, symbol: str) -> dict | None:
        """Get Reddit sentiment counts from cache or provider."""
        if self._redis is not None:
            import json
            cached = self._redis.get(f'reddit:{symbol}:counts')
            if cached:
                try:
                    return json.loads(cached)
                except (ValueError, TypeError):
                    pass
        return None

    def _run_finbert(self, headlines: list[str]) -> list[float]:
        """Run FinBERT batch inference on headlines.

        Returns list of sentiment scores in [0, 1].
        Batch size = 32 for CPU efficiency.
        Uses a module-level singleton so the ~100 MB model is loaded once.
        """
        try:
            import torch

            tokenizer, model = _get_finbert()

            scores = []
            batch_size = 32

            for i in range(0, len(headlines), batch_size):
                batch = headlines[i:i + batch_size]
                inputs = tokenizer(
                    batch, padding=True, truncation=True,
                    max_length=512, return_tensors='pt',
                )
                with torch.no_grad():
                    outputs = model(**inputs)
                    probs = torch.nn.functional.softmax(outputs.logits, dim=-1)

                # FinBERT classes: positive, negative, neutral
                for prob in probs:
                    pos = float(prob[0])
                    neg = float(prob[1])
                    # Score: positive sentiment maps to higher score
                    scores.append(pos * 1.0 + (1 - pos - neg) * 0.5 + neg * 0.0)

            return scores

        except ImportError:
            self._log.warning('transformers_not_available')
            raise
        except Exception as exc:
            self._log.warning('finbert_inference_failed', error=str(exc))
            raise

    @staticmethod
    def _vader_score(texts: list[str]) -> float | None:
        """Fallback: VADER sentiment for short-form text."""
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            analyzer = SentimentIntensityAnalyzer()

            compounds = []
            for text in texts:
                scores = analyzer.polarity_scores(text)
                compounds.append(scores['compound'])

            if not compounds:
                return None

            # Compound ranges [-1, 1], normalize to [0, 1]
            avg = float(np.mean(compounds))
            return (avg + 1.0) / 2.0

        except ImportError:
            return None
