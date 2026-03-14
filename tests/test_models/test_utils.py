import numpy as np

from app.utils.normalization import (
    cross_sectional_percentile_rank,
    min_max_cap,
    percentile_rank,
    zscore_sigmoid,
)


class TestNormalization:
    def test_percentile_rank_middle(self):
        history = np.arange(100, dtype=float)
        assert abs(percentile_rank(50.0, history) - 0.51) < 0.05

    def test_percentile_rank_extremes(self):
        history = np.arange(100, dtype=float)
        assert percentile_rank(0.0, history) < 0.05
        assert percentile_rank(99.0, history) > 0.95

    def test_percentile_rank_insufficient_data(self):
        assert percentile_rank(5.0, np.array([1.0])) == 0.5

    def test_zscore_sigmoid_center(self):
        history = np.random.normal(0, 1, 1000)
        result = zscore_sigmoid(0.0, history)
        assert 0.45 < result < 0.55

    def test_zscore_sigmoid_extremes(self):
        history = np.random.normal(0, 1, 1000)
        assert zscore_sigmoid(3.0, history) > 0.9
        assert zscore_sigmoid(-3.0, history) < 0.1

    def test_min_max_cap(self):
        assert min_max_cap(5.0, 0.0, 10.0) == 0.5
        assert min_max_cap(0.0, 0.0, 10.0) == 0.0
        assert min_max_cap(10.0, 0.0, 10.0) == 1.0
        assert min_max_cap(15.0, 0.0, 10.0) == 1.0  # capped
        assert min_max_cap(-5.0, 0.0, 10.0) == 0.0  # capped

    def test_cross_sectional_rank(self):
        values = {'SPY': 0.8, 'QQQ': 0.5, 'XLK': 0.2}
        ranks = cross_sectional_percentile_rank(values)
        assert ranks['SPY'] > ranks['QQQ'] > ranks['XLK']
        assert all(0.0 <= r <= 1.0 for r in ranks.values())


class TestEncryption:
    def test_encrypt_decrypt(self):
        from cryptography.fernet import Fernet
        from app.utils.encryption import decrypt_value, encrypt_value, mask_secret

        key = Fernet.generate_key().decode()
        original = 'my-secret-api-key-12345'
        encrypted = encrypt_value(original, key)
        decrypted = decrypt_value(encrypted, key)
        assert decrypted == original
        assert encrypted != original

    def test_mask_secret(self):
        from app.utils.encryption import mask_secret
        assert mask_secret('abcdefgh5f3a') == '••••5f3a'
        assert mask_secret('ab') == '••••'
