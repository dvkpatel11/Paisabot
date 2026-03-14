from datetime import date

from app.models.etf_universe import ETFUniverse


class TestETFUniverse:
    def test_create_etf(self, db_session):
        etf = ETFUniverse(
            symbol='SPY',
            name='SPDR S&P 500 ETF Trust',
            sector='Broad Market',
            aum_bn=530.0,
            avg_daily_vol_m=25000.0,
            spread_est_bps=0.3,
            liquidity_score=1.00,
            inception_date=date(1993, 1, 22),
            options_market=True,
            mt5_symbol='SPY.US',
        )
        db_session.add(etf)
        db_session.commit()

        result = ETFUniverse.query.filter_by(symbol='SPY').first()
        assert result is not None
        assert result.name == 'SPDR S&P 500 ETF Trust'
        assert result.mt5_symbol == 'SPY.US'
        assert result.is_active is True

    def test_symbol_unique(self, db_session):
        e1 = ETFUniverse(symbol='QQQ', name='QQQ', sector='Tech')
        e2 = ETFUniverse(symbol='QQQ', name='QQQ dupe', sector='Tech')
        db_session.add(e1)
        db_session.commit()
        db_session.add(e2)

        from sqlalchemy.exc import IntegrityError
        import pytest
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()
