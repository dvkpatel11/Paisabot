"""Financial Modeling Prep (FMP) data provider.

Fetches fundamental data, financial ratios, earnings calendar, and
company profiles for stocks.

Free tier: 250 req/day. Use batch endpoints where possible.
Docs: https://site.financialmodelingprep.com/developer/docs

Environment variable: FMP_API_KEY
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import structlog

logger = structlog.get_logger()


class FMPProvider:
    """Fetch fundamentals, ratios, and earnings from Financial Modeling Prep."""

    BASE_URL = 'https://financialmodelingprep.com/api/v3'

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._log = logger.bind(provider='fmp')

    # ── helpers ──────────────────────────────────────────────────

    def _get(self, path: str, params: dict | None = None) -> list | dict | None:
        """Make authenticated GET request to FMP API."""
        import requests

        url = f'{self.BASE_URL}/{path}'
        req_params = {'apikey': self._api_key}
        if params:
            req_params.update(params)

        try:
            resp = requests.get(url, params=req_params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and 'Error Message' in data:
                self._log.error('fmp_api_error', path=path, error=data['Error Message'])
                return None
            return data
        except Exception as exc:
            self._log.error('fmp_request_failed', path=path, error=str(exc))
            return None

    @staticmethod
    def _safe_decimal(value, default=None) -> Decimal | None:
        """Convert value to Decimal, returning default on failure."""
        if value is None:
            return default
        try:
            return Decimal(str(value))
        except Exception:
            return default

    # ── company profile ──────────────────────────────────────────

    def get_profile(self, symbol: str) -> dict | None:
        """Fetch company profile (sector, industry, market cap, beta, etc.).

        Returns dict with normalized keys or None on failure.
        """
        data = self._get(f'profile/{symbol}')
        if not data or not isinstance(data, list) or len(data) == 0:
            return None

        p = data[0]
        return {
            'symbol': symbol,
            'name': p.get('companyName', ''),
            'sector': p.get('sector', 'Unknown'),
            'industry': p.get('industry', ''),
            'market_cap_bn': self._safe_decimal(
                p.get('mktCap', 0) / 1e9 if p.get('mktCap') else 0,
            ),
            'beta': self._safe_decimal(p.get('beta')),
            'avg_daily_vol_m': self._safe_decimal(
                p.get('volAvg', 0) * float(p.get('price', 0)) / 1e6
                if p.get('volAvg') and p.get('price')
                else 0,
            ),
            'description': p.get('description', ''),
            'exchange': p.get('exchangeShortName', ''),
            'ipo_date': p.get('ipoDate'),
        }

    def get_profiles_batch(self, symbols: list[str]) -> dict[str, dict]:
        """Fetch profiles for multiple symbols in one request.

        FMP supports comma-separated symbols on the profile endpoint.
        Returns {symbol: profile_dict}.
        """
        if not symbols:
            return {}

        # FMP batch limit ~50 symbols per request
        results = {}
        for i in range(0, len(symbols), 50):
            batch = symbols[i:i + 50]
            joined = ','.join(batch)
            data = self._get(f'profile/{joined}')
            if data and isinstance(data, list):
                for p in data:
                    sym = p.get('symbol', '')
                    results[sym] = {
                        'name': p.get('companyName', ''),
                        'sector': p.get('sector', 'Unknown'),
                        'industry': p.get('industry', ''),
                        'market_cap_bn': self._safe_decimal(
                            p.get('mktCap', 0) / 1e9 if p.get('mktCap') else 0,
                        ),
                        'beta': self._safe_decimal(p.get('beta')),
                        'avg_daily_vol_m': self._safe_decimal(
                            p.get('volAvg', 0) * float(p.get('price', 0)) / 1e6
                            if p.get('volAvg') and p.get('price')
                            else 0,
                        ),
                    }

        self._log.info('profiles_fetched', count=len(results))
        return results

    # ── financial ratios ─────────────────────────────────────────

    def get_ratios(self, symbol: str, period: str = 'annual') -> dict | None:
        """Fetch latest financial ratios (P/E, P/B, ROE, etc.).

        Args:
            symbol: Stock ticker
            period: 'annual' or 'quarter'

        Returns dict with normalized ratio keys or None.
        """
        data = self._get(f'ratios/{symbol}', params={'period': period, 'limit': 1})
        if not data or not isinstance(data, list) or len(data) == 0:
            return None

        r = data[0]
        return {
            'symbol': symbol,
            'period': r.get('period', ''),
            'pe_ratio': self._safe_decimal(r.get('priceEarningsRatio')),
            'pb_ratio': self._safe_decimal(r.get('priceBookValueRatio')),
            'ps_ratio': self._safe_decimal(r.get('priceToSalesRatio')),
            'roe': self._safe_decimal(r.get('returnOnEquity')),
            'debt_to_equity': self._safe_decimal(r.get('debtEquityRatio')),
            'profit_margin': self._safe_decimal(r.get('netProfitMargin')),
            'dividend_yield': self._safe_decimal(r.get('dividendYield')),
            'current_ratio': self._safe_decimal(r.get('currentRatio')),
            'quick_ratio': self._safe_decimal(r.get('quickRatio')),
        }

    def get_key_metrics(self, symbol: str, period: str = 'annual') -> dict | None:
        """Fetch key metrics (forward P/E, revenue growth, etc.)."""
        data = self._get(f'key-metrics/{symbol}', params={'period': period, 'limit': 1})
        if not data or not isinstance(data, list) or len(data) == 0:
            return None

        m = data[0]
        return {
            'symbol': symbol,
            'forward_pe': self._safe_decimal(m.get('peRatio')),
            'revenue_per_share': self._safe_decimal(m.get('revenuePerShare')),
            'eps': self._safe_decimal(m.get('netIncomePerShare')),
            'book_value_per_share': self._safe_decimal(m.get('bookValuePerShare')),
            'free_cash_flow_per_share': self._safe_decimal(m.get('freeCashFlowPerShare')),
        }

    # ── income statement (for growth) ────────────────────────────

    def get_income_growth(self, symbol: str) -> dict | None:
        """Compute YoY revenue and earnings growth from income statements.

        Fetches last 2 annual periods and computes growth rates.
        """
        data = self._get(f'income-statement/{symbol}', params={'period': 'annual', 'limit': 2})
        if not data or not isinstance(data, list) or len(data) < 2:
            return None

        current = data[0]
        prior = data[1]

        rev_current = current.get('revenue', 0) or 0
        rev_prior = prior.get('revenue', 0) or 0
        eps_current = current.get('eps', 0) or 0
        eps_prior = prior.get('eps', 0) or 0

        rev_growth = None
        if rev_prior and rev_prior != 0:
            rev_growth = self._safe_decimal((rev_current - rev_prior) / abs(rev_prior))

        eps_growth = None
        if eps_prior and eps_prior != 0:
            eps_growth = self._safe_decimal((eps_current - eps_prior) / abs(eps_prior))

        return {
            'symbol': symbol,
            'revenue_growth_yoy': rev_growth,
            'earnings_growth_yoy': eps_growth,
            'revenue_current': rev_current,
            'revenue_prior': rev_prior,
        }

    # ── earnings calendar & surprises ────────────────────────────

    def get_earnings_calendar(
        self,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[dict]:
        """Fetch earnings calendar for a date range.

        Returns list of {symbol, date, eps_estimated, eps_actual, ...}.
        """
        params = {}
        if from_date:
            params['from'] = from_date.isoformat()
        if to_date:
            params['to'] = to_date.isoformat()

        data = self._get('earning_calendar', params=params)
        if not data or not isinstance(data, list):
            return []

        results = []
        for e in data:
            results.append({
                'symbol': e.get('symbol', ''),
                'date': e.get('date', ''),
                'eps_estimated': self._safe_decimal(e.get('epsEstimated')),
                'eps_actual': self._safe_decimal(e.get('eps')),
                'revenue_estimated': e.get('revenueEstimated'),
                'revenue_actual': e.get('revenue'),
                'fiscal_period': e.get('fiscalDateEnding', ''),
            })

        self._log.info('earnings_calendar_fetched', count=len(results))
        return results

    def get_earnings_surprises(self, symbol: str, limit: int = 4) -> list[dict]:
        """Fetch recent earnings surprises for a symbol.

        Returns list of {date, eps_estimated, eps_actual, surprise_pct}.
        """
        data = self._get(f'earnings-surprises/{symbol}')
        if not data or not isinstance(data, list):
            return []

        results = []
        for e in data[:limit]:
            estimated = e.get('estimatedEarning')
            actual = e.get('actualEarningResult')
            surprise = None
            if estimated and actual and estimated != 0:
                surprise = self._safe_decimal((actual - estimated) / abs(estimated))

            results.append({
                'date': e.get('date', ''),
                'eps_estimated': self._safe_decimal(estimated),
                'eps_actual': self._safe_decimal(actual),
                'surprise_pct': surprise,
            })

        return results

    # ── analyst estimates ────────────────────────────────────────

    def get_analyst_estimates(self, symbol: str) -> dict | None:
        """Fetch consensus analyst estimates (next quarter)."""
        data = self._get(f'analyst-estimates/{symbol}', params={'period': 'quarter', 'limit': 1})
        if not data or not isinstance(data, list) or len(data) == 0:
            return None

        e = data[0]
        return {
            'symbol': symbol,
            'date': e.get('date', ''),
            'estimated_revenue_avg': e.get('estimatedRevenueAvg'),
            'estimated_eps_avg': self._safe_decimal(e.get('estimatedEpsAvg')),
            'number_of_analysts': e.get('numberAnalystsEstimatedRevenue'),
        }

    # ── short interest ───────────────────────────────────────────

    def get_short_interest(self, symbol: str) -> dict | None:
        """Fetch short interest data for a symbol (via key metrics TTM)."""
        # FMP doesn't have a dedicated short interest endpoint on free tier.
        # Approximate via float shares and short-interest fields in profile.
        profile = self.get_profile(symbol)
        if not profile:
            return None

        return {
            'symbol': symbol,
            'short_interest_pct': None,  # requires premium endpoint
            'float_shares_m': None,
        }

    # ── aggregate fundamentals fetch ─────────────────────────────

    def get_full_fundamentals(self, symbol: str) -> dict | None:
        """Fetch all fundamental data for a symbol in one call batch.

        Combines profile, ratios, growth, and earnings into a single dict
        suitable for updating StockUniverse fields.
        """
        profile = self.get_profile(symbol)
        ratios = self.get_ratios(symbol, period='annual')
        growth = self.get_income_growth(symbol)
        key_metrics = self.get_key_metrics(symbol, period='annual')
        surprises = self.get_earnings_surprises(symbol, limit=4)

        result = {
            'symbol': symbol,
            'fetched_at': datetime.now(timezone.utc).isoformat(),
        }

        if profile:
            result.update({
                'name': profile['name'],
                'sector': profile['sector'],
                'industry': profile['industry'],
                'market_cap_bn': profile['market_cap_bn'],
                'beta': profile['beta'],
            })

        if ratios:
            result.update({
                'pe_ratio': ratios['pe_ratio'],
                'pb_ratio': ratios['pb_ratio'],
                'ps_ratio': ratios['ps_ratio'],
                'roe': ratios['roe'],
                'debt_to_equity': ratios['debt_to_equity'],
                'profit_margin': ratios['profit_margin'],
                'dividend_yield': ratios['dividend_yield'],
            })

        if key_metrics:
            result['forward_pe'] = key_metrics.get('forward_pe')

        if growth:
            result.update({
                'revenue_growth_yoy': growth['revenue_growth_yoy'],
                'earnings_growth_yoy': growth['earnings_growth_yoy'],
            })

        if surprises:
            result['last_earnings_surprise'] = surprises[0].get('surprise_pct')
            # Average surprise over last 3 quarters (if available)
            valid = [s['surprise_pct'] for s in surprises[:3] if s.get('surprise_pct') is not None]
            if valid:
                result['earnings_surprise_3q_avg'] = self._safe_decimal(
                    sum(float(v) for v in valid) / len(valid),
                )

        self._log.info(
            'full_fundamentals_fetched',
            symbol=symbol,
            fields=len(result),
        )
        return result
