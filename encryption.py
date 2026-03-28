"""
Encryption module for Reliable FTP
Security: Fernet symmetric encryption (AES-128-CBC with HMAC)

Fernet provides:
- AES-128-CBC encryption for confidentiality
- HMAC-SHA256 for message authentication
- Timestamp-based replay attack prevention
"""

from cryptography.fernet import Fernet

# Shared encryption key (in production, use key exchange protocol)
# Generated with: Fernet.generate_key()
KEY = b'Zr5rj1L6wF1c4z9sH0K2mYk7TqP8xA3vB6D9uE2nC4g='

cipher = Fernet(KEY)


def encrypt_data(data):
    """Encrypt data using Fernet (AES-128-CBC + HMAC)"""
    return cipher.encrypt(data)


def decrypt_data(data):
    """Decrypt data using Fernet"""
    return cipher.decrypt(data)