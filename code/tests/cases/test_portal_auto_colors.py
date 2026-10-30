import pytest

from management_portal import serve_management_portal as portal
from management_portal import user_preferences


def test_auto_colors_are_account_scoped_and_partial_updates_preserve_other_preferences(isolated_portal_case):
    """自动配色按账户保存，部分更新保留其他外观偏好，清空后回到默认值"""
    with isolated_portal_case():
        user_preferences._set_portal_user_preferences("alice", {"appearance": {
            "motion": "reduced", "language": "en",
            "auto_colors": {"accent": "#7952B3", "quote_color": "#163A4A", "bubble_intensity": 24},
        }})
        user_preferences._set_portal_user_preferences("alice", {"appearance": {"auto_colors": {"bubble_intensity": 100}}})
        appearance = user_preferences._portal_user_preferences("alice")["appearance"]
        assert appearance == {"motion": "reduced", "language": "en", "auto_colors": {"accent": "#7952b3", "quote_color": "#163a4a", "bubble_intensity": 100}}
        assert "auto_colors" not in user_preferences._portal_user_preferences("bob")["appearance"]
        user_preferences._set_portal_user_preferences("alice", {"appearance": {"auto_colors": None}})
        assert user_preferences._portal_user_preferences("alice")["appearance"]["auto_colors"] == {"accent": "", "quote_color": "", "bubble_intensity": 18}


@pytest.mark.parametrize("value, expected", [
    ({"accent": "url(https://example.com/)", "quote_color": "red;--text:white", "bubble_intensity": True}, {"accent": "", "bubble_intensity": 18}),
    ({"quote_color": "#163A4A"}, {"accent": "", "quote_color": "#163a4a", "bubble_intensity": 18}),
    ({"accent": "#ABCDEF", "bubble_intensity": 24.5}, {"accent": "#abcdef", "bubble_intensity": 25}),
    ({"accent": "#123", "bubble_intensity": -100}, {"accent": "", "bubble_intensity": 0}),
    ({"accent": "red;--text:white", "bubble_intensity": 200}, {"accent": "", "bubble_intensity": 100}),
    ({"bubble_intensity": float("nan")}, {"accent": "", "bubble_intensity": 18}),
    (None, {"accent": "", "bubble_intensity": 18}),
])
def test_invalid_color_preferences_never_become_css(value, expected):
    """非法颜色值被清空，气泡强度被规整到 0 到 100，不会写入 CSS"""
    assert user_preferences._normalize_portal_user_preferences({"appearance": {"auto_colors": value}})["appearance"]["auto_colors"] == {"quote_color": "", **expected}
