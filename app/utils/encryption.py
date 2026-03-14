from cryptography.fernet import Fernet


def get_fernet(key: str) -> Fernet:
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_value(value: str, fernet_key: str) -> str:
    f = get_fernet(fernet_key)
    return f.encrypt(value.encode()).decode()


def decrypt_value(encrypted: str, fernet_key: str) -> str:
    f = get_fernet(fernet_key)
    return f.decrypt(encrypted.encode()).decode()


def mask_secret(value: str) -> str:
    """Display masked format: ••••5f3a (last 4 chars visible)."""
    if len(value) <= 4:
        return '••••'
    return '••••' + value[-4:]
