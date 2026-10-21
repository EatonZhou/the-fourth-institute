"""Identity storage consumes each password reset atomically across connections.

:module: platform.data
"""
from concurrent.futures import ThreadPoolExecutor
from management_portal.portal_password_reset import PortalPasswordResetStore


def test_reset_token_can_be_consumed_by_only_one_connection():
    """多个连接并发使用同一个密码重置令牌时只有一个成功"""
    token = PortalPasswordResetStore().issue("engineer")

    def consume(index):
        try:
            return PortalPasswordResetStore().consume(token)
        except PermissionError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(consume, range(16)))
    assert results.count("engineer") == 1
