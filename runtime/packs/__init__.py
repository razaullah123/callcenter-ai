"""Packs: domain code referenced by name from agent configuration (tool policies, skills).

Importing this package registers every pack's hooks (runtime.tools.hooks). A pack is the only place where one
customer's tool names and result shapes may appear; the harness itself stays generic.
"""

from . import hmg  # noqa: F401
