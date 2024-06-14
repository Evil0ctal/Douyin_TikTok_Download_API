import warnings
import functools


def deprecated(message):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            warnings.warn(
                f"{func.__name__} is deprecated: {message}",
                DeprecationWarning,
                stacklevel=2
            )
            return await func(*args, **kwargs)

        return wrapper

    return decorator