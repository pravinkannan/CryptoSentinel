"""
Custom exception definitions for the Crypto Sentinel.
"""

class CryptoSentinelException(Exception):
    """Base exception for all Crypto Sentinel errors."""
    pass

class ExchangeAPIError(CryptoSentinelException):
    """Raised when an exchange API call fails."""
    pass

class AIEvaluationError(CryptoSentinelException):
    """Raised when the AI evaluation model fails or times out."""
    pass

class ConfigurationError(CryptoSentinelException):
    """Raised when configuration validation fails."""
    pass

class DatabaseError(CryptoSentinelException):
    """Raised when a local database operation fails."""
    pass
