from app.models.system_config import SystemConfig


class TestSystemConfig:
    def test_create_config(self, db_session):
        config = SystemConfig(
            category='weights',
            key='weight_trend',
            value='0.25',
            value_type='float',
            description='Trend factor weight',
        )
        db_session.add(config)
        db_session.commit()

        result = SystemConfig.query.filter_by(
            category='weights', key='weight_trend'
        ).first()
        assert result is not None
        assert result.value == '0.25'
        assert result.value_type == 'float'

    def test_unique_constraint(self, db_session):
        c1 = SystemConfig(category='risk', key='max_drawdown', value='-0.15')
        c2 = SystemConfig(category='risk', key='max_drawdown', value='-0.10')
        db_session.add(c1)
        db_session.commit()
        db_session.add(c2)

        from sqlalchemy.exc import IntegrityError
        import pytest
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


class TestConfigLoader:
    def test_get_from_redis(self, config_loader, redis_mock):
        redis_mock.hset('config:weights', 'weight_trend', '0.25')
        assert config_loader.get('weights', 'weight_trend') == '0.25'

    def test_get_fallback_to_db(self, config_loader, db_session):
        config = SystemConfig(
            category='risk', key='max_drawdown', value='-0.15',
        )
        db_session.add(config)
        db_session.commit()

        result = config_loader.get('risk', 'max_drawdown')
        assert result == '-0.15'

    def test_get_default(self, config_loader):
        result = config_loader.get('nonexistent', 'key', default='fallback')
        assert result == 'fallback'

    def test_get_float(self, config_loader, redis_mock):
        redis_mock.hset('config:risk', 'vol_target', '0.12')
        assert config_loader.get_float('risk', 'vol_target') == 0.12

    def test_get_bool(self, config_loader, redis_mock):
        redis_mock.hset('config:risk', 'vol_scaling_enabled', 'true')
        assert config_loader.get_bool('risk', 'vol_scaling_enabled') is True

        redis_mock.hset('config:risk', 'vol_scaling_enabled', 'false')
        assert config_loader.get_bool('risk', 'vol_scaling_enabled') is False

    def test_kill_switch(self, config_loader, redis_mock):
        assert config_loader.is_kill_switch_active('trading') is False
        config_loader.set_kill_switch('trading', True)
        assert config_loader.is_kill_switch_active('trading') is True
        config_loader.set_kill_switch('trading', False)
        assert config_loader.is_kill_switch_active('trading') is False

    def test_warm_cache(self, config_loader, db_session, redis_mock):
        configs = [
            SystemConfig(category='weights', key='weight_trend', value='0.25'),
            SystemConfig(category='risk', key='max_drawdown', value='-0.15'),
        ]
        db_session.add_all(configs)
        db_session.commit()

        config_loader.warm_cache()

        assert redis_mock.hget('config:weights', 'weight_trend') == b'0.25'
        assert redis_mock.hget('config:risk', 'max_drawdown') == b'-0.15'

    def test_set(self, config_loader, db_session, redis_mock):
        config_loader.set('portfolio', 'max_positions', '15', updated_by='test')

        # Check DB
        row = SystemConfig.query.filter_by(
            category='portfolio', key='max_positions'
        ).first()
        assert row is not None
        assert row.value == '15'

        # Check Redis
        assert redis_mock.hget('config:portfolio', 'max_positions') == b'15'
