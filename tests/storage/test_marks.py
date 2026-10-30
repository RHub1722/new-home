from datetime import UTC, datetime

from homealize.storage import marks, repo
from tests.factories import make_listing

T0 = datetime(2026, 10, 30, 5, 0, tzinfo=UTC)


def add(db, lid):
    repo.upsert_listing(db, make_listing(id=lid), status="active", filter_reason=None, now=T0)


def test_marks_are_added_once_listed_and_removed(db):
    add(db, "a")
    add(db, "b")
    assert marks.add_mark(db, "a", marks.FAV, now=T0) is True
    assert marks.add_mark(db, "a", marks.FAV, now=T0) is False
    assert marks.add_mark(db, "b", marks.HIDDEN, now=T0) is True
    assert marks.marked(db, marks.FAV) == {"a"}
    assert marks.marked(db, marks.HIDDEN) == {"b"}
    assert marks.remove_mark(db, "a", marks.FAV) is True
    assert marks.remove_mark(db, "a", marks.FAV) is False
    assert marks.marked(db, marks.FAV) == set()


def test_mark_time_is_stored_in_utc(db):
    add(db, "a")
    marks.add_mark(db, "a", marks.FAV, now=T0)
    assert db.execute("SELECT created_at FROM user_marks").fetchone()[0] == T0.isoformat()
