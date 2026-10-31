"""Browser storage, refresh channels and the public site object use canonical identifiers only.

:design: decision_15aab18c0ab3
"""
from pathlib import Path
import subprocess


def test_browser_state_and_site_object_use_only_canonical_identifiers():
    """浏览器状态、刷新通道与公开站点对象只使用规范标识，配置的旧别名不生效

    站点配置中的旧存储别名不会重定向页面状态、主题、语言与导览会话，也不迁移或删除旧内容；格式标签只精确匹配规范名称，旧前缀或畸形映射都不被接受；
    刷新广播通道与锁名使用规范标识；公开站点对象滤掉私有字段与部署信息，兼容字段固定为空且不可修改。

    :module: operations.deployment
    """
    script = Path(__file__).with_name("javascript") / "portal_canonical_identifiers.cjs"
    result = subprocess.run(["node", "--test", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
