"""Errors shown to the user as-is (no traceback)."""


class PyhyiError(Exception):
    """Base class for expected failures: bad config, not a git repo, and so on."""


class ConfigError(PyhyiError):
    """Invalid pyhyi configuration."""


class ComparisonBaseError(ConfigError):
    """The requested comparison has no usable Git base."""


class GitError(PyhyiError):
    """A git command failed or git is unavailable."""
