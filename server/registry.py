"""The server's roster of connected users.

This is the shared mutable state of the whole server, and it is the reason the
server is single-threaded asyncio rather than thread-per-client. Every mutation
below is a plain ``dict`` operation with no lock anywhere, which is correct
precisely because there is only ever one event loop touching it.

Why that is sound rather than lucky: asyncio runs one task at a time and only
switches at an ``await``. None of these methods await, so each one is atomic
with respect to every other task by construction. A ``threading.Lock`` here
would be worse than useless -- it would suggest the code is safe in a threaded
server when the surrounding design is not, and it would be the kind of lock
somebody eventually forgets to take.

The invariant enforced here: **a nickname maps to at most one session, and a
session has at most one nickname.** Both directions are stored together so the
two can never drift.
"""

from __future__ import annotations

from typing import Any, Iterator

__all__ = ["User", "UserRegistry"]


class User:
    """One connected user: a nickname and whatever the session layer needs.

    ``session`` is deliberately typed loosely -- the registry does not care
    whether it holds a server-side session driver or a test double, only that it
    can be handed back to the caller that registered it.
    """

    __slots__ = ("nickname", "session", "session_id", "joined_at", "peer")

    def __init__(
        self,
        nickname: str,
        session: Any,
        *,
        session_id: str = "",
        joined_at: str = "",
        peer: str = "",
    ) -> None:
        self.nickname = nickname
        self.session = session
        self.session_id = session_id
        self.joined_at = joined_at
        self.peer = peer

    def to_payload(self) -> dict[str, str]:
        """The public view of a user, as it appears in a USER_LIST."""
        return {"nick": self.nickname, "joined_at": self.joined_at}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"User({self.nickname!r}, peer={self.peer!r})"


class UserRegistry:
    """Nickname -> session mapping with uniqueness enforced on both axes."""

    __slots__ = ("_by_nickname", "_by_session")

    def __init__(self) -> None:
        self._by_nickname: dict[str, User] = {}
        self._by_session: dict[str, User] = {}

    def __len__(self) -> int:
        return len(self._by_nickname)

    def __contains__(self, nickname: object) -> bool:
        return nickname in self._by_nickname

    def __iter__(self) -> Iterator[User]:
        return iter(self._by_nickname.values())

    # -- lookup -----------------------------------------------------------

    def get(self, nickname: str) -> User | None:
        """Return the user holding ``nickname``, or ``None``."""
        return self._by_nickname.get(nickname)

    def by_session(self, session_id: str) -> User | None:
        """Return the user behind a session id, or ``None``."""
        return self._by_session.get(session_id)

    def nicknames(self) -> list[str]:
        """Every nickname, sorted.

        Sorted rather than insertion-ordered so two clients that connect in
        different orders still render the same USER_LIST. A list that reshuffles
        between refreshes looks like a bug in the UI even when it is not.
        """
        return sorted(self._by_nickname)

    def snapshot(self) -> list[dict[str, str]]:
        """The public payload for a USER_LIST."""
        return [self._by_nickname[nick].to_payload() for nick in self.nicknames()]

    # -- mutation ---------------------------------------------------------

    def reserve(self, nickname: str, session: Any, *, peer: str = "") -> User | None:
        """Claim ``nickname`` for ``session``.

        Returns:
            The new :class:`User`, or ``None`` if the nickname is already held.
            Returning rather than raising keeps the caller's duplicate-nickname
            path a plain ``if``, since "taken" is an expected outcome during a
            handshake, not an exceptional one.
        """
        if nickname in self._by_nickname:
            return None

        user = User(nickname, session, peer=peer)
        self._by_nickname[nickname] = user
        return user

    def attach_session(self, user: User, session_id: str) -> None:
        """Index ``user`` by their newly assigned session id.

        Separate from :meth:`reserve` because the id is minted *after* the
        nickname is claimed: the claim must be the atomic step that decides who
        wins a race for a name, and the id depends on that outcome.
        """
        if user.session_id:
            self._by_session.pop(user.session_id, None)
        user.session_id = session_id
        self._by_session[session_id] = user

    def rename(self, user: User, nickname: str) -> bool:
        """Change ``user``'s nickname in place.

        Returns:
            ``False`` if the target nickname is taken by somebody else. Renaming
            a user to the nickname they already hold succeeds without touching
            the mapping, so a client repeating ``/nick`` is not an error.
        """
        if nickname == user.nickname:
            return True
        if nickname in self._by_nickname:
            return False

        del self._by_nickname[user.nickname]
        user.nickname = nickname
        self._by_nickname[nickname] = user
        return True

    def release(self, user: User) -> bool:
        """Remove ``user``, if they are still the holder of their nickname.

        The identity check matters: a client that reconnected and reclaimed a
        freed nickname would otherwise have its entry deleted by the *old*
        session's cleanup, which runs whenever that connection's reader loop
        finally notices the socket is gone. The stale cleanup must not evict the
        new occupant.
        """
        current = self._by_nickname.get(user.nickname)
        if current is not user:
            return False

        del self._by_nickname[user.nickname]
        if user.session_id:
            self._by_session.pop(user.session_id, None)
        return True

    def release_session(self, session_id: str) -> User | None:
        """Remove whoever holds ``session_id`` and return them."""
        user = self._by_session.get(session_id)
        if user is None:
            return None
        self.release(user)
        return user
