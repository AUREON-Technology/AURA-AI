"""Sandbox policy: strict boundaries for engineer agent tool execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class SandboxPolicy:
    """Security policy governing engineer agent sandbox execution.

    These rules are enforced by the execution pipeline before each tool call.
    Violations cause the tool call to be rejected with a SecurityError
    rather than silently proceeding.
    """

    # Filesystem boundaries
    max_workspace_depth: int = 10  # max nested directory levels
    prohibit_above_root: bool = True  # cannot write outside workspace root
    allow_listed_extensions: tuple[str, ...] = ()  # if set, only these extensions allowed
    deny_listed_extensions: tuple[str, ...] = (
        ".exe",
        ".dll",
        ".so",
        ".app",
    )  # dangerous file types

    # Command restrictions
    prohibit_commands: tuple[str, ...] = (
        "rm -rf",
        "dd ",
        "> /dev",
        "curl |",
        "wget |",
    )  # destructive or network-stream commands

    max_command_args: int = 50  # max arguments a command can have
    max_command_line_length: int = 4000  # chars

    # Network restrictions
    network_enabled: bool = False  # no outbound network from sandbox
    allowed_origins: tuple[str, ...] = ()  # if set, only these origins allowed

    # Timeout
    default_timeout_s: float = 120.0  # max seconds per command execution

    @property
    def is_network_allowed(self) -> bool:
        return self.network_enabled

    @property
    def is_command_allowed(self, command: str) -> bool:
        """Check if a command violates any policy rule."""
        cmd_lower = command.lower().strip()
        for prohibited in self.prohibit_commands:
            if cmd_lower.startswith(prohibited.lower()):
                return False
        # Check argument count
        args = command.split()
        if len(args) > self.max_command_args:
            return False
        # Check line length
        if len(command) > self.max_command_line_length:
            return False
        # Check denied extensions in arguments
        for arg in args:
            for denied in self.deny_listed_extensions:
                if arg.endswith(denied):
                    return False
        return True


@dataclass
class GitSafetyPolicy:
    """Git safety policy for engineer agent repository operations."""

    allow_force_push: bool = False  # never force push
    require_signoff: bool = True  # require signed off-by tag
    allow_delete_branch: bool = False  # never delete protected branches
    protected_branches: tuple[str, ...] = ("main", "master", "develop")
    max_commit_messages: int = 1  # max concurrent uncommitted changes
    require_description: bool = True  # commit messages must have a description

    def is_branch_protected(self, branch: str) -> bool:
        return branch.lower() in [b.lower() for b in self.protected_branches]

    def can_force_push(self, target_branch: str) -> bool:
        return (self.allow_force_push and not self.is_branch_protected(target_branch))

    def can_delete_branch(self, branch: str) -> bool:
        return self.allow_delete_branch and not self.is_branch_protected(branch)