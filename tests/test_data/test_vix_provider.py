import sys
from datetime import date
from unittest.mock import MagicMock, patch
from types import ModuleType

import fakeredis
import pandas as pd
import pytest

from app.data.vix_provider import VIXProvider


def _make_mock_pdr(return_value=None, side_effect=None):
    """Create a mock pandas_datareader.data module."""
    mock_pdr = ModuleType('pandas_datareader')
    mock_data = ModuleType('pandas_datareader.data')
    mock_reader = MagicMock(return_value=return_value, side_effect=side_effect)
    mock_data.DataReader = mock_reader
    mock_pdr.data = mock_data
    return mock_pdr, mock_data, mock_reader


class TestVIXProvider:
    @pytest.fixture
    def redis_mock(self):
        return fakeredis.FakeRedis(decode_responses=True)

    @pytest.fixture
    def provider(self, redis_mock):
        return VIXProvider(redis_client=redis_mock)

    def test_get_latest_vix_from_cache(self, provider, redis_mock):
        redis_mock.set('vix:latest', '18.5')
        result = provider.get_latest_vix()
        assert result == 18.5

    def test_get_latest_vix_cache_miss_fetches_fred(self, provider):
        with patch.object(provider, '_fetch_from_fred', return_value=19.0):
            result = provider.get_latest_vix()
        assert result == 19.0

    def test_get_latest_vix_cache_miss_fred_fails(self, provider):
        with patch.object(provider, '_fetch_from_fred', return_value=None):
            result = provider.get_latest_vix()
        assert result is None

    def test_get_latest_vix_caches_result(self, provider, redis_mock):
        with patch.object(provider, '_fetch_from_fred', return_value=20.0):
            provider.get_latest_vix()
        assert redis_mock.get('vix:latest') == '20.0'

    def test_get_vix_history(self, provider):
        mock_df = pd.DataFrame(
            {'VIXCLS': [18.5, 19.0, 17.5]},
            index=pd.to_datetime(['2024-01-02', '2024-01-03', '2024-01-04']),
        )
        mock_pdr, mock_data, _ = _make_mock_pdr(return_value=mock_df)
        with patch.dict(sys.modules, {
            'pandas_datareader': mock_pdr,
            'pandas_datareader.data': mock_data,
        }):
            result = provider.get_vix_history(
                date(2024, 1, 1), date(2024, 1, 5),
            )
        assert len(result) == 3
        assert list(result.columns) == ['date', 'vix_close']

    def test_get_vix_history_error_returns_empty(self, provider):
        mock_pdr, mock_data, _ = _make_mock_pdr(
            side_effect=Exception('FRED unavailable'),
        )
        with patch.dict(sys.modules, {
            'pandas_datareader': mock_pdr,
            'pandas_datareader.data': mock_data,
        }):
            result = provider.get_vix_history(
                date(2024, 1, 1), date(2024, 1, 5),
            )
        assert result.empty

    def test_no_redis_still_works(self):
        provider = VIXProvider(redis_client=None)
        with patch.object(provider, '_fetch_from_fred', return_value=21.0):
            result = provider.get_latest_vix()
        assert result == 21.0
