"""
The ambient context.

Everything this library records is attributed through the context, so a
mistake here is not a wrong number — it is one tenant's data filed under
another's name.
"""

from __future__ import annotations

from shipit_watcher.context import bind, current_context


class TestTheDefaultContextIsNotShared:
    """
    A ContextVar default is one object shared by everything that never set its
    own, and ``result`` is a mutable dict. With a shared instance, output
    written while no trace was active stayed there and the next caller read it
    back — one tenant's answer handed to another, in the library whose whole
    purpose is keeping them apart.
    """

    def test_output_written_unbound_does_not_reach_the_next_caller(self):
        with bind(user_id="alice") as ctx:
            ctx.set_output("alice's private answer")

        with bind(user_id="bob") as ctx:
            assert ctx.result == {}

    def test_the_ambient_context_stays_empty(self):
        with bind(user_id="alice") as ctx:
            ctx.set_output("alice's private answer")

        assert current_context().result == {}

    def test_each_unbound_read_is_its_own_context(self):
        first = current_context()
        first.set_output("scribbled on a throwaway")
        assert current_context().result == {}
