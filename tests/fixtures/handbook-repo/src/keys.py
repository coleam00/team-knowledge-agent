def rotate_api_keys() -> None:
    """Rotate API keys by issuing a replacement, updating consumers, and revoking
    the old key after every consumer has switched to the replacement.
    """
