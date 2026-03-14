import numpy as np
import pandas as pd
import pytest

from app.portfolio.exposure import ExposureAnalyzer


@pytest.fixture
def analyzer():
    return ExposureAnalyzer()


SECTOR_MAP = {
    'XLK': 'Technology',
    'QQQ': 'Technology',
    'XLV': 'Health Care',
    'XLE': 'Energy',
    'XLF': 'Financials',
    'SPY': 'Broad',
}


def _make_prices(symbols, n=100, seed=42):
    np.random.seed(seed)
    idx = pd.date_range('2025-01-01', periods=n, freq='B')
    data = {}
    for s in symbols:
        data[s] = 100 * np.exp(np.cumsum(np.random.normal(0.0005, 0.01, n)))
    return pd.DataFrame(data, index=idx)


# ── sector exposure ─────────────────────────────────────────────────

class TestSectorExposure:
    def test_basic_exposure(self, analyzer):
        weights = {'XLK': 0.03, 'QQQ': 0.04, 'XLV': 0.05, 'XLE': 0.02}
        result = analyzer.sector_exposure(weights, SECTOR_MAP)
        assert result['Technology'] == pytest.approx(0.07)
        assert result['Health Care'] == pytest.approx(0.05)
        assert result['Energy'] == pytest.approx(0.02)

    def test_sorted_descending(self, analyzer):
        weights = {'XLK': 0.01, 'XLV': 0.05, 'XLE': 0.03}
        result = analyzer.sector_exposure(weights, SECTOR_MAP)
        values = list(result.values())
        assert values == sorted(values, reverse=True)

    def test_unknown_sector(self, analyzer):
        weights = {'UNKNOWN': 0.05}
        result = analyzer.sector_exposure(weights, {})
        assert result['Unknown'] == 0.05

    def test_empty_weights(self, analyzer):
        result = analyzer.sector_exposure({}, SECTOR_MAP)
        assert result == {}


# ── concentration metrics ───────────────────────────────────────────

class TestConcentration:
    def test_equal_weight(self, analyzer):
        weights = {'XLK': 0.20, 'XLV': 0.20, 'XLE': 0.20, 'XLF': 0.20, 'SPY': 0.20}
        result = analyzer.concentration_metrics(weights)
        assert result['hhi'] == pytest.approx(0.20, abs=0.01)  # 5 equal = 0.20
        assert result['n_positions'] == 5
        assert result['top1_pct'] == pytest.approx(0.20, abs=0.01)

    def test_concentrated(self, analyzer):
        weights = {'XLK': 0.90, 'XLV': 0.10}
        result = analyzer.concentration_metrics(weights)
        assert result['hhi'] > 0.5
        assert result['top1_pct'] == 0.9

    def test_single_position(self, analyzer):
        weights = {'XLK': 0.50}
        result = analyzer.concentration_metrics(weights)
        assert result['hhi'] == 1.0
        assert result['n_positions'] == 1

    def test_empty_weights(self, analyzer):
        result = analyzer.concentration_metrics({})
        assert result['n_positions'] == 0
        assert result['hhi'] == 0.0


# ── portfolio beta ──────────────────────────────────────────────────

class TestPortfolioBeta:
    def test_computes_beta(self, analyzer):
        prices = _make_prices(['XLK', 'XLV', 'SPY'])
        weights = {'XLK': 0.30, 'XLV': 0.30}
        beta = analyzer.portfolio_beta(weights, prices)
        assert beta is not None
        assert -2.0 < beta < 3.0

    def test_no_benchmark(self, analyzer):
        prices = _make_prices(['XLK', 'XLV'])
        beta = analyzer.portfolio_beta({'XLK': 0.50}, prices, benchmark='SPY')
        assert beta is None

    def test_insufficient_data(self, analyzer):
        idx = pd.date_range('2025-01-01', periods=10, freq='B')
        prices = pd.DataFrame({'XLK': range(10), 'SPY': range(10)}, index=idx)
        beta = analyzer.portfolio_beta({'XLK': 0.50}, prices)
        assert beta is None


# ── full report ─────────────────────────────────────────────────────

class TestFullReport:
    def test_report_includes_all_fields(self, analyzer):
        weights = {'XLK': 0.04, 'XLV': 0.03, 'XLE': 0.02}
        prices = _make_prices(['XLK', 'XLV', 'XLE', 'SPY'])
        report = analyzer.full_report(weights, SECTOR_MAP, prices, 100_000)

        assert 'sector_exposures' in report
        assert 'concentration' in report
        assert 'cash_pct' in report
        assert 'invested_pct' in report
        assert 'portfolio_value' in report
        assert 'beta' in report
        assert report['cash_pct'] == pytest.approx(0.91, abs=0.01)

    def test_report_without_prices(self, analyzer):
        weights = {'XLK': 0.04}
        report = analyzer.full_report(weights, SECTOR_MAP)
        assert 'beta' not in report
        assert report['invested_pct'] == pytest.approx(0.04)
