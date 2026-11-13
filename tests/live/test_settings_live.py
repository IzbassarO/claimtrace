from claimtrace.core.settings import get_settings


def test_env_is_complete():
    assert get_settings().problems() == []
