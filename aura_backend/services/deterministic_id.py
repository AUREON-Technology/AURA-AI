import hashlib

def get_deterministic_id(value: str) -> int:
    """Generates a stable, deterministic 64-bit unsigned integer ID from a string using SHA-256."""
    h = hashlib.sha256(value.encode('utf-8')).digest()
    # Take the first 8 bytes and convert to a 64-bit unsigned integer
    return int.from_bytes(h[:8], byteorder='big') & 0xFFFFFFFFFFFFFFFF
