"""The roster's invariants, tested without a socket in sight.

The registry is where the server's concurrency claim is cashed in: it holds no
lock because a single event loop is the only writer. What can still go wrong is
the *logic* -- two nicknames pointing at one session, or a stale cleanup evicting
the session that took the name over -- and that is what these tests pin down.

The identity check in :meth:`UserRegistry.release` is the subtle one. It is easy
to write, easy to delete as redundant, and its absence is invisible until a
client reconnects fast enough to reclaim its own nickname.
"""

from __future__ import annotations

import unittest

from server.registry import User, UserRegistry


class ReserveTests(unittest.TestCase):
    """Claiming a nickname."""

    def test_reserve_returns_a_user_and_registers_it(self) -> None:
        registry = UserRegistry()

        user = registry.reserve("budi", session="s1", peer="127.0.0.1:1")

        self.assertIsNotNone(user)
        self.assertEqual(user.nickname, "budi")
        self.assertIs(registry.get("budi"), user)
        self.assertEqual(len(registry), 1)

    def test_reserving_a_held_nickname_returns_none_and_changes_nothing(self) -> None:
        registry = UserRegistry()
        first = registry.reserve("budi", session="s1")

        second = registry.reserve("budi", session="s2")

        self.assertIsNone(second)
        # The first holder must survive: losing the race must not evict the winner.
        self.assertIs(registry.get("budi"), first)
        self.assertEqual(len(registry), 1)

    def test_two_different_nicknames_both_register(self) -> None:
        registry = UserRegistry()

        registry.reserve("budi", session="s1")
        registry.reserve("sari", session="s2")

        self.assertEqual(registry.nicknames(), ["budi", "sari"])


class SessionIndexTests(unittest.TestCase):
    """The second axis of the mapping: session id -> user."""

    def test_attach_session_makes_the_user_reachable_by_id(self) -> None:
        registry = UserRegistry()
        user = registry.reserve("budi", session="s1")

        registry.attach_session(user, "session-a")

        self.assertIs(registry.by_session("session-a"), user)
        self.assertEqual(user.session_id, "session-a")

    def test_reattaching_drops_the_previous_id(self) -> None:
        registry = UserRegistry()
        user = registry.reserve("budi", session="s1")
        registry.attach_session(user, "session-a")

        registry.attach_session(user, "session-b")

        self.assertIsNone(registry.by_session("session-a"))
        self.assertIs(registry.by_session("session-b"), user)

    def test_release_session_returns_the_user_and_removes_both_entries(self) -> None:
        registry = UserRegistry()
        user = registry.reserve("budi", session="s1")
        registry.attach_session(user, "session-a")

        released = registry.release_session("session-a")

        self.assertIs(released, user)
        self.assertEqual(len(registry), 0)
        self.assertIsNone(registry.by_session("session-a"))

    def test_release_session_of_an_unknown_id_is_none(self) -> None:
        registry = UserRegistry()

        self.assertIsNone(registry.release_session("nope"))


class RenameTests(unittest.TestCase):
    """Changing a nickname in place, keeping the session."""

    def test_rename_moves_the_user_to_the_new_nickname(self) -> None:
        registry = UserRegistry()
        user = registry.reserve("budi", session="s1")
        registry.attach_session(user, "session-a")

        self.assertTrue(registry.rename(user, "budi2"))

        self.assertIsNone(registry.get("budi"))
        self.assertIs(registry.get("budi2"), user)
        self.assertIs(registry.by_session("session-a"), user)
        self.assertEqual(len(registry), 1)

    def test_rename_to_a_taken_nickname_fails_and_leaves_both_alone(self) -> None:
        registry = UserRegistry()
        budi = registry.reserve("budi", session="s1")
        sari = registry.reserve("sari", session="s2")

        self.assertFalse(registry.rename(budi, "sari"))

        self.assertIs(registry.get("budi"), budi)
        self.assertIs(registry.get("sari"), sari)

    def test_renaming_to_the_same_nickname_succeeds(self) -> None:
        registry = UserRegistry()
        user = registry.reserve("budi", session="s1")

        # A client repeating /nick budi is not an error, so this must not be
        # reported as one -- and it must not delete the entry on the way out.
        self.assertTrue(registry.rename(user, "budi"))
        self.assertIs(registry.get("budi"), user)


class ReleaseTests(unittest.TestCase):
    """Removing a user, and refusing to remove their successor."""

    def test_release_removes_the_user_from_both_indexes(self) -> None:
        registry = UserRegistry()
        user = registry.reserve("budi", session="s1")
        registry.attach_session(user, "session-a")

        self.assertTrue(registry.release(user))

        self.assertEqual(len(registry), 0)
        self.assertIsNone(registry.get("budi"))
        self.assertIsNone(registry.by_session("session-a"))

    def test_release_refuses_when_the_nickname_now_belongs_to_another_session(self) -> None:
        registry = UserRegistry()
        stale = registry.reserve("budi", session="old")
        registry.release(stale)
        # A new client reconnects and reclaims the freed nickname before the old
        # connection's cleanup has run.
        fresh = registry.reserve("budi", session="new")

        self.assertFalse(registry.release(stale))

        self.assertIs(registry.get("budi"), fresh)
        self.assertEqual(len(registry), 1)

    def test_release_of_an_unknown_nickname_is_false(self) -> None:
        registry = UserRegistry()
        stranger = User("ghost", session=None)

        self.assertFalse(registry.release(stranger))


class SnapshotTests(unittest.TestCase):
    """What a USER_LIST actually shows."""

    def test_snapshot_is_sorted_by_nickname(self) -> None:
        registry = UserRegistry()
        registry.reserve("sari", session="s2")
        registry.reserve("budi", session="s1")
        registry.reserve("andi", session="s3")

        # Sorted rather than insertion-ordered: two clients that connected in a
        # different order must still render the same list.
        self.assertEqual([entry["nick"] for entry in registry.snapshot()], ["andi", "budi", "sari"])

    def test_snapshot_exposes_only_the_public_fields(self) -> None:
        registry = UserRegistry()
        registry.reserve("budi", session="s1", peer="127.0.0.1:5000")

        entry = registry.snapshot()[0]

        self.assertEqual(set(entry), {"nick", "joined_at"})

    def test_iteration_yields_users(self) -> None:
        registry = UserRegistry()
        user = registry.reserve("budi", session="s1")

        self.assertEqual([u.nickname for u in registry], ["budi"])
        self.assertIs(next(iter(registry)), user)

    def test_contains_checks_by_nickname(self) -> None:
        registry = UserRegistry()
        registry.reserve("budi", session="s1")

        self.assertIn("budi", registry)
        self.assertNotIn("sari", registry)


if __name__ == "__main__":
    unittest.main()
