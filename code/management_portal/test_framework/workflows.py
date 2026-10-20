BUILTIN = {"name": "test", "title": "Test", "path": "/development?view=test", "description": "测试执行、验证证据与 TAU 工作台"}


def verification_flow():
    return {
        "name": "test:tau:verify", "title": "TAU 模块验收",
        "prompt": "执行本次 TAU 模块验收：先真实操作指定 DEV 页面，保留步骤与截图证据，再定位原因。严格按本次输入的 Case、权限、预算与结果协议工作；不要递归启动 TAU 或伪造通过。结果必须回到当前 Run 的正式证据目录。",
    }


def definitions(scope):
    return [verification_flow()] if scope.startswith("portal-tau:") else []
