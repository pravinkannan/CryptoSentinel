def njit(*args, **kwargs):
    def wrapper(func):
        return func
    if len(args) == 1 and callable(args[0]):
        return args[0]
    return wrapper

def jit(*args, **kwargs):
    return njit(*args, **kwargs)

class prange:
    def __init__(self, *args):
        self.r = range(*args)
    def __iter__(self):
        return iter(self.r)
