"""Canonical OpenViking scope layout.

OpenViking is not a single flat bucket: it ships a fixed three-scope taxonomy
and a set of per-user subdirectories, and its own extraction pipeline writes
memory documents into ``memories/<memory_type>/<subject>/``.

Observed on the hosted service (``OV_STATUS`` v0.5.0.10)::

    viking://agent/
        endpoints/                     shared communication endpoint config
        payments/                      shared payment capability config
        skills/                        account-wide skill registry (SKILL.md)
        tools/                         shared tool configuration
    viking://resources/                account-shared knowledge base
        <category>/...                 admin/, ops/, ...
    viking://user/<user>/
        memories/<type>/<subject>/<doc>.md
                                       self long-term memory; ``<type>`` is one
                                       of profile, preferences, entities,
                                       events, cases, experiences, trajectories
        peers/<peer>/memories/...      peer-scoped memory (written only when a
                                       message carries an explicit peer id)
        resources/<index>/...          private knowledge base; VeADK convention
                                       is ``resources/<kb_index>/``
        skills/                        user skill registry (Claude Skills)
        sessions/<session_id>/         messages.jsonl + history/archive_NNN/
        settings/user_config.json      memory policy and add targets
        privacy/                       user-scoped sensitive config snapshots

GPA used to dump everything into ``user/<user>/resources`` and read memory from
the whole user scope.  This module is the single source of truth for the URI a
GPA concept maps to, so the retriever, the writers, the browser APIs and the
setup scripts cannot drift apart again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ROOT = "viking://"
AGENT_ROOT = f"{ROOT}agent"
SHARED_ROOT = f"{ROOT}resources"
USER_ROOT = f"{ROOT}user"

#: Memory types OpenViking extracts by default
#: (``user/<user>/settings/user_config.json`` -> ``memory_policy.memory_types``).
MEMORY_TYPES: tuple[str, ...] = (
    "profile",
    "preferences",
    "entities",
    "events",
    "cases",
    "experiences",
    "trajectories",
)

MEMORY_TYPE_LABELS: dict[str, str] = {
    "profile": "画像",
    "preferences": "偏好",
    "entities": "实体",
    "events": "事件",
    "cases": "案例",
    "experiences": "经验",
    "trajectories": "轨迹",
}

#: Scope keys returned by :meth:`OvLayout.scope_of`.
SCOPE_SELF_MEMORY = "self_memory"
SCOPE_PEER_MEMORY = "peer_memory"
SCOPE_PERSONAL_KB = "personal_kb"
SCOPE_SHARED_KB = "shared_kb"
SCOPE_AGENT_SKILL = "agent_skill"
SCOPE_USER_SKILL = "user_skill"
SCOPE_SESSION = "session"
SCOPE_SETTINGS = "settings"
SCOPE_PRIVACY = "privacy"
SCOPE_AGENT = "agent"
SCOPE_USER = "user"
SCOPE_UNKNOWN = "unknown"

DEFAULT_KB_INDEX = "uploads"

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_.@-]+$")


class LayoutError(ValueError):
    """Raised when a configured path segment cannot be used in a viking:// URI."""


def safe_segment(value: str, *, name: str) -> str:
    """Validate one path segment before it becomes part of a ``viking://`` URI."""
    segment = str(value or "").strip()
    if not segment:
        raise LayoutError(f"{name} must not be empty")
    if segment in {".", ".."} or not _SAFE_SEGMENT.match(segment):
        raise LayoutError(
            f"{name} must be a single safe path segment (letters, digits, '.', '_', '@', '-'): {segment!r}"
        )
    return segment


def parse_segments(raw: str | None, *, name: str) -> tuple[str, ...]:
    """Parse a comma-separated settings value into validated path segments."""
    if not raw:
        return ()
    seen: list[str] = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        segment = safe_segment(part, name=name)
        if segment not in seen:
            seen.append(segment)
    return tuple(seen)


def _within(uri: str, prefix: str) -> bool:
    return uri == prefix or uri.startswith(f"{prefix}/")


@dataclass(frozen=True)
class OvLayout:
    """The ``viking://`` directories owned by one GPA identity."""

    ov_user: str
    ov_peer: str | None = None
    personal_kb_indexes: tuple[str, ...] = field(default_factory=tuple)
    shared_kb_categories: tuple[str, ...] = field(default_factory=tuple)

    # ---------------------------------------------------------------- scopes
    @property
    def agent_root(self) -> str:
        return AGENT_ROOT

    @property
    def agent_skills(self) -> str:
        return f"{AGENT_ROOT}/skills"

    @property
    def agent_tools(self) -> str:
        return f"{AGENT_ROOT}/tools"

    @property
    def shared_root(self) -> str:
        return SHARED_ROOT

    @property
    def user_root(self) -> str:
        return f"{USER_ROOT}/{self.ov_user}"

    @property
    def memories(self) -> str:
        return f"{self.user_root}/memories"

    @property
    def peers(self) -> str:
        return f"{self.user_root}/peers"

    @property
    def resources(self) -> str:
        return f"{self.user_root}/resources"

    @property
    def skills(self) -> str:
        return f"{self.user_root}/skills"

    @property
    def sessions(self) -> str:
        return f"{self.user_root}/sessions"

    @property
    def settings_uri(self) -> str:
        return f"{self.user_root}/settings/user_config.json"

    @property
    def privacy(self) -> str:
        return f"{self.user_root}/privacy"

    # ---------------------------------------------------------- parametrized
    def memory_type(self, memory_type: str) -> str:
        """``user/<u>/memories/<type>`` – one OV memory type bucket."""
        return f"{self.memories}/{safe_segment(memory_type, name='memory_type')}"

    def peer_memories(self, peer: str | None = None) -> str:
        """``user/<u>/peers/<peer>/memories`` – memory attributed to a peer."""
        peer_id = peer or self.ov_peer
        if not peer_id:
            raise LayoutError("peer memory requires an ov_peer identity")
        return f"{self.peers}/{safe_segment(peer_id, name='peer')}/memories"

    def personal_kb(self, index: str) -> str:
        """``user/<u>/resources/<index>`` – one personal knowledge base."""
        return f"{self.resources}/{safe_segment(index, name='personal_kb_index')}"

    def shared_kb(self, category: str) -> str:
        """``viking://resources/<category>`` – one shared knowledge base."""
        return f"{SHARED_ROOT}/{safe_segment(category, name='shared_kb_category')}"

    # ------------------------------------------------------- search scopes
    def memory_roots(self, *, include_peers: bool = True) -> list[str]:
        """Directories a long-term-memory search should cover.

        ``memories`` holds self memory.  ``peers`` is added as a whole (instead
        of enumerating peers first) so peer memory written by other clients is
        still recoverable; OpenViking scopes the result to the caller's key.
        """
        roots = [self.memories]
        if include_peers and self.ov_peer:
            roots.append(self.peers)
        return roots

    def personal_kb_roots(self) -> list[str]:
        """Personal KB targets: configured indexes, else the whole scope.

        Reading the scope root keeps documents written before GPA introduced
        KB indexes (or by other clients) visible; writes always use an index.
        """
        if self.personal_kb_indexes:
            return [self.personal_kb(index) for index in self.personal_kb_indexes]
        return [self.resources]

    def shared_kb_roots(self) -> list[str]:
        """Shared KB targets: configured categories, else ``viking://resources``."""
        if self.shared_kb_categories:
            return [self.shared_kb(category) for category in self.shared_kb_categories]
        return [SHARED_ROOT]

    def skill_roots(self) -> list[str]:
        """Skill registries the agent may draw capabilities from."""
        return [self.agent_skills, self.skills]

    # ------------------------------------------------------------ write map
    def write_personal_kb(self, index: str | None = None) -> str:
        """Directory new personal documents are written into."""
        chosen = index or (self.personal_kb_indexes[0] if self.personal_kb_indexes else DEFAULT_KB_INDEX)
        return self.personal_kb(chosen)

    def write_shared_kb(self, category: str | None = None) -> str:
        """Directory new shared documents are written into.

        ``viking://resources`` **is** the shared library: admins write it and
        every user reads it, so there is no per-role folder (``admin/``) and no
        redundant ``shared/`` wrapper.  A category is used only when the
        deployment opts into topic folders via ``OV_SHARED_KB_CATEGORIES``.
        """
        chosen = category or (self.shared_kb_categories[0] if self.shared_kb_categories else None)
        return self.shared_kb(chosen) if chosen else SHARED_ROOT

    def artifact_card(self, task_id: str) -> str:
        """Task summary card so a finished task is recallable as knowledge."""
        return f"{self.write_personal_kb('artifacts')}/{safe_segment(task_id, name='task_id')}.md"

    # -------------------------------------------------------- classification
    def _peer_path(self, uri: str) -> tuple[str, str] | None:
        """``(peer_id, path under memories)`` for a peer-memory URI."""
        base = f"{self.peers}/"
        uri = (uri or "").rstrip("/")
        if not uri.startswith(base):
            return None
        parts = uri[len(base) :].split("/", 2)
        if len(parts) < 2 or parts[1] != "memories":
            return None
        return parts[0], (parts[2] if len(parts) > 2 else "")

    def scope_of(self, uri: str) -> str:
        uri = (uri or "").rstrip("/")
        if _within(uri, self.agent_skills):
            return SCOPE_AGENT_SKILL
        if _within(uri, AGENT_ROOT):
            return SCOPE_AGENT
        if _within(uri, SHARED_ROOT):
            return SCOPE_SHARED_KB
        if _within(uri, self.memories):
            return SCOPE_SELF_MEMORY
        if _within(uri, self.peers):
            return SCOPE_PEER_MEMORY if self._peer_path(uri) else SCOPE_USER
        if _within(uri, self.resources):
            return SCOPE_PERSONAL_KB
        if _within(uri, self.skills):
            return SCOPE_USER_SKILL
        if _within(uri, self.sessions):
            return SCOPE_SESSION
        if _within(uri, self.privacy):
            return SCOPE_PRIVACY
        if _within(uri, f"{self.user_root}/settings"):
            return SCOPE_SETTINGS
        if uri == self.user_root:
            return SCOPE_USER
        return SCOPE_UNKNOWN

    def memory_type_of(self, uri: str) -> str | None:
        """Extract ``<type>`` from a memory document URI, if it has one."""
        scope = self.scope_of(uri)
        if scope == SCOPE_SELF_MEMORY:
            rel = uri.rstrip("/")[len(self.memories) + 1 :]
        elif scope == SCOPE_PEER_MEMORY:
            parsed = self._peer_path(uri)
            if parsed is None:
                return None
            rel = parsed[1]
        else:
            return None
        head = rel.split("/", 1)[0]
        return head or None

    def peer_of(self, uri: str) -> str | None:
        """Extract the peer id from a peer-memory URI, if it has one."""
        parsed = self._peer_path(uri)
        return parsed[0] if parsed else None


def build_layout(settings, identity) -> OvLayout:
    """Build the layout for a resolved GPA identity (see ``identity.py``)."""
    return OvLayout(
        ov_user=identity.ov_user,
        ov_peer=identity.ov_peer,
        personal_kb_indexes=parse_segments(
            getattr(settings, "ov_personal_kb_indexes", ""), name="ov_personal_kb_indexes"
        ),
        shared_kb_categories=parse_segments(
            getattr(settings, "ov_shared_kb_categories", ""), name="ov_shared_kb_categories"
        ),
    )
