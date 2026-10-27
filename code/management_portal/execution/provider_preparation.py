"""Public runtime preparation diagnostics: fixed phases, never process output."""
from contextlib import contextmanager

MESSAGES = {
    "download": "无法获取运行环境包或版本，请检查下载网络及软件源后重试。",
    "dependency_install": "运行依赖安装失败，请检查磁盘空间、安装权限及软件包来源后重试。",
    "executable_validation": "运行程序校验失败，请检查平台兼容性及运行依赖后重试。",
    "service_activation": "运行环境已安装，但服务启用失败。请检查当前实例的服务登记、权限及服务日志后重试。",
    "prerequisites": "系统运行依赖准备失败，请检查依赖安装权限和下载网络后重试。",
}


class PreparationError(RuntimeError):
    def __init__(self, phase, cause):
        self.phase = phase
        # Even custom exception class names may include untrusted text.
        self.kind = ("permission" if isinstance(cause, PermissionError) else
                     "missing" if isinstance(cause, FileNotFoundError) else
                     "timeout" if isinstance(cause, TimeoutError) else "operation_failed")
        self.rollback_failed = False
        super().__init__(MESSAGES[phase])

    def public(self):
        return {"phase": self.phase, "message": str(self),
                "diagnostic": {"phase": self.phase, "kind": self.kind,
                               "rollback_failed": self.rollback_failed}}


@contextmanager
def phase(name):
    try:
        yield
    except PreparationError:
        raise
    except Exception as exc:
        raise PreparationError(name, exc) from exc


def diagnosed(name):
    from functools import wraps
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            with phase(name):
                return function(*args, **kwargs)
        return call
    return decorate
