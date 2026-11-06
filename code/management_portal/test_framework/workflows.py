BUILTIN = {"name": "test", "title": "Test", "path": "/development?view=test", "description": "测试执行、验证证据与 TAU 工作台"}


def verification_flow():
    return {
        "name": "test:tau:verify", "title": "TAU 模块使用报告",
        "prompt": "像真实用户一样使用本次指定部署的页面完成任务，以运行目标与预检身份为准，记录过程、问题清单和性能体验。问题分产品问题、用例问题、执行偏差、工具问题，严重程度分阻断、影响使用、小问题。缺截图、耗时或因果材料注明未记录；短暂重启和断线恢复后继续。遵守本次权限与操作范围，不递归启动 TAU。遵守当前汇报设置，不创建独立汇报产物；普通答复仍返回框架必需的 PORTAL_TAU_RESULT 执行回执，并在当前 Run 目录保留已有观察材料。",
    }


def definitions(scope):
    return [verification_flow()] if scope.startswith("portal-tau:") else []
