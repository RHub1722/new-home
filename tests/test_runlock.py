import pytest

from homealize.runlock import LockBusyError, hold_lock


def test_second_holder_is_refused_until_the_first_releases(tmp_path):
    path = tmp_path / "data" / "run.lock"
    with hold_lock(path):
        assert path.exists()
        with pytest.raises(LockBusyError):
            with hold_lock(path):
                pass
    with hold_lock(path):
        pass


def test_lock_is_released_when_the_body_fails(tmp_path):
    path = tmp_path / "run.lock"
    with pytest.raises(RuntimeError):
        with hold_lock(path):
            raise RuntimeError("сбой")
    with hold_lock(path):
        pass


def test_different_files_do_not_block_each_other(tmp_path):
    with hold_lock(tmp_path / "run.lock"):
        with hold_lock(tmp_path / "serve.lock"):
            pass
