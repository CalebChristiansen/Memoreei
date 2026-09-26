from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("memoreei")
except PackageNotFoundError:  # running from a checkout without being installed
    __version__ = "0+unknown"
