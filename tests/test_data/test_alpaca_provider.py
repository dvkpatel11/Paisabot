from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.data.alpaca_provider import AlpacaDataProvider, RateLimiter


class TestRateLimiter:
    def test_allows_calls_under_limit(self):
        limiter = RateLimiter(max_calls=5, period=60.0)
        for _ in range(5):
            limiter.wait()  # should not block

    def test_tracks_call_count(self):
        limiter = RateLimiter(max_calls=3, period=60.0)
        limiter.wait()
        limiter.wait()
        assert len(limiter._calls) == 2


class TestAlpacaDataProvider:
    @pytest.fixture
    def mock_client(self):
        with patch(
            'app.data.alpaca_provider.StockHistoricalDataClient'
        ) as mock_cls:
            client = MagicMock()
            mock_cls.return_value = client
            yield client

    @pytest.fixture
    def provider(self, mock_client):
        return AlpacaDataProvider(api_key='test', secret_key='test')

    def test_get_daily_bars_empty(self, provider, mock_client):
        mock_bars = MagicMock()
        mock_bars.df = pd.DataFrame()
        mock_client.get_stock_bars.return_value = mock_bars

        result = provider.get_daily_bars(
            'SPY', date(2024, 1, 1), date(2024, 1, 31),
        )
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_get_daily_bars_with_data(self, provider, mock_client):
        index = pd.MultiIndex.from_tuples(
            [('SPY', datetime(2024, 1, 2, tzinfo=timezone.utc))],
            names=['symbol', 'timestamp'],
        )
        df = pd.DataFrame(
            {
                'open': [470.0],
                'high': [475.0],
                'low': [469.0],
                'close': [474.0],
                'volume': [80000000],
                'vwap': [472.0],
                'trade_count': [500000],
            },
            index=index,
        )
        mock_bars = MagicMock()
        mock_bars.df = df
        mock_client.get_stock_bars.return_value = mock_bars

        result = provider.get_daily_bars(
            'SPY', date(2024, 1, 1), date(2024, 1, 31),
        )
        assert not result.empty
        assert list(result.columns) == [
            'timestamp', 'open', 'high', 'low', 'close',
            'volume', 'vwap', 'trade_count',
        ]
        assert float(result.iloc[0]['close']) == 474.0

    def test_get_latest_bar_none(self, provider, mock_client):
        mock_client.get_stock_latest_bar.return_value = {}
        result = provider.get_latest_bar('SPY')
        assert result is None

    def test_get_latest_bar_with_data(self, provider, mock_client):
        bar = MagicMock()
        bar.timestamp = datetime(2024, 1, 2, tzinfo=timezone.utc)
        bar.open = 470.0
        bar.high = 475.0
        bar.low = 469.0
        bar.close = 474.0
        bar.volume = 80000000
        bar.vwap = 472.0
        bar.trade_count = 500000
        mock_client.get_stock_latest_bar.return_value = {'SPY': bar}

        result = provider.get_latest_bar('SPY')
        assert result is not None
        assert result['symbol'] == 'SPY'
        assert result['close'] == 474.0
        assert result['volume'] == 80000000

    def test_get_latest_quote_none(self, provider, mock_client):
        mock_client.get_stock_latest_quote.return_value = {}
        result = provider.get_latest_quote('SPY')
        assert result is None

    def test_get_latest_quote_with_data(self, provider, mock_client):
        quote = MagicMock()
        quote.bid_price = 473.50
        quote.ask_price = 474.50
        quote.timestamp = datetime(2024, 1, 2, tzinfo=timezone.utc)
        mock_client.get_stock_latest_quote.return_value = {'SPY': quote}

        result = provider.get_latest_quote('SPY')
        assert result is not None
        assert result['bid'] == 473.50
        assert result['ask'] == 474.50
        assert result['mid'] == 474.0
        assert result['spread_bps'] > 0

    def test_get_multi_bars_empty(self, provider, mock_client):
        mock_bars = MagicMock()
        mock_bars.df = pd.DataFrame()
        mock_client.get_stock_bars.return_value = mock_bars

        result = provider.get_multi_bars(
            ['SPY', 'QQQ'], date(2024, 1, 1), date(2024, 1, 31),
        )
        assert 'SPY' in result
        assert 'QQQ' in result
        assert result['SPY'].empty
