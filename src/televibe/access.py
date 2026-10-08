"""How much an agent may change (section 9)."""

import enum


class Access(enum.Enum):
    """The same name does not give the same guarantee across providers (see SPEC.md section 9)."""

    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    FULL = "full"
