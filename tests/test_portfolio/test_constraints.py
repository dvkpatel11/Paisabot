import pytest
from unittest.mock import MagicMock

from app.portfolio.constraints import PortfolioConstraints


class TestPortfolioConstraints:
    def test_defaults(self):
        c = PortfolioConstraints()
        assert c.max_positions == 10
        assert c.max_position_size == 0.05
        assert c.min_position_size == 0.01
        assert c.max_sector_exposure == 0.25
        assert c.turnover_limit_pct == 0.50
        assert c.cash_buffer_pct == 0.05
        assert c.objective == 'max_sharpe'
        assert c.vol_target == 0.12

    def test_custom_values(self):
        c = PortfolioConstraints(max_positions=5, cash_buffer_pct=0.10)
        assert c.max_positions == 5
        assert c.cash_buffer_pct == 0.10

    def test_from_config(self):
        loader = MagicMock()
        loader.get_float.return_value = None
        loader.get.return_value = None

        c = PortfolioConstraints.from_config(loader)
        # Should fall back to defaults
        assert c.max_positions == 10
        assert c.objective == 'max_sharpe'

    def test_from_config_with_values(self):
        loader = MagicMock()

        def mock_get_float(cat, key):
            vals = {
                ('portfolio', 'max_positions'): 8.0,
                ('portfolio', 'max_position_pct'): 0.08,
                ('portfolio', 'turnover_limit_pct'): 0.30,
            }
            return vals.get((cat, key))

        loader.get_float.side_effect = mock_get_float
        loader.get.return_value = 'min_vol'

        c = PortfolioConstraints.from_config(loader)
        assert c.max_positions == 8
        assert c.max_position_size == 0.08
        assert c.turnover_limit_pct == 0.30
        assert c.objective == 'min_vol'
