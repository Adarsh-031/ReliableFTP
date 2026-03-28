"""
Utility functions for Reliable FTP
"""

import hashlib

# Chunk size for file transfer (4KB)
CHUNK_SIZE = 4096


def checksum(data):
    """Calculate MD5 checksum for data integrity verification"""
    return hashlib.md5(data).hexdigest()


def verify_checksum(data, expected_checksum):
    """Verify data integrity by comparing checksums"""
    return checksum(data) == expected_checksum