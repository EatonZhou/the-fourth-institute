BUILTIN = {"name": "test", "title": "Test", "path": "/development?view=test", "description": "测试执行、验证证据与 TAU 工作台"}


def verification_flow():
    return {
        "name": "test:tau:verify", "title": "TAU 模块使用报告",
        "prompt": "像真实用户一样使用本次指定部署的页面完成任务，以运行目标与预检身份为准，报告过程、问题清单和性能体验。问题分产品问题、用例问题、执行偏差、工具问题，严重程度分阻断、影响使用、小问题。缺截图、耗时或因果材料注明未记录；短暂重启和断线恢复后继续。遵守本次权限与操作范围，不递归启动 TAU。将使用报告和已有材料保存到当前 Run 目录。",
    }


def definitions(scope):
    return [verification_flow()] if scope.startswith("portal-tau:") else []
