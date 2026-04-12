"""Event priority configuration."""

# Priority levels for events:
# 1 = high priority (e.g., git commits, CLI commands, task mutations)
# 2 = medium priority (e.g., file changes)
# 3 = low priority (default)

EVENT_PRIORITY = {
    "git_commit": 1,
    "cli_command": 1,
    "task_created": 1,
    "task_answered": 1,
    "task_cancelled": 1,
    "task_retried": 1,
    "file_change": 2,
    "file_created": 2,
    "file_modified": 2,
    "file_deleted": 2,
}