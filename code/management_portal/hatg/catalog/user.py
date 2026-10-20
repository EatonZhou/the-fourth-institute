"""User instructions (decision_6eac174d85bf), executed by Portal Web."""
from __future__ import annotations

from ..declarations import FIXED, ON_USER, InstructionDeclaration as D, P, R


def _d(name, decides, description, parameters=(), result=(), *, mutates=False, target=""):
    return D(name=name, category="user", owner="web", description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates, target=target,
             decides=decides)


USER = R("user", "object", "用户资料：用户名、显示名称、恢复邮箱、头像、账号状态和 User 分类（category：portal_admin、member 或 intern）")
USER_CATEGORIES = ("portal_admin", "member", "intern")


def _user_id(description="User 的用户名"):
    return R("user_id", description=description)


DECLARATIONS = (
    _d("CreateUserInvitation", FIXED, "邀请一个新 User；人类与外部助手共用此入口。", (
        _user_id("被邀请 User 的用户名"), R("secret_sha256", description="客户端生成的随机邀请密钥的 SHA256，不提交原文"),
        P("display_name", description="新账号的显示名称；省略时用用户名"),
        P("expires_in_days", "integer", description="有效期 1-30 天，默认 7"),
    ), (R("invitation", "object", "新邀请：invitation_id、username、display_name、invited_by、created_at、expires_at（Unix 秒）、"
          "status，不含密钥摘要"),), mutates=True),
    _d("ListUserInvitations", FIXED, "列出用户邀请及领取状态。", (), (
        R("invitations", "array", "全部邀请，新的在前；每项 invitation_id、username、display_name、invited_by、created_at、"
          "expires_at 与 status（pending、accepted、revoked、expired）"),)),
    _d("RevokeUserInvitation", FIXED, "撤销尚未领取的邀请。", (
        R("invitation_id", description="要撤销的邀请，取 ListUserInvitations 返回的 invitation_id"),
    ), (R("invitation", "object", "撤销后的邀请记录；已领取或已撤销的保持原状态"),), mutates=True, target="invitation_id"),
    _d("CreateUserAccessToken", ON_USER, "为一位 User 创建个人访问凭据。", (
        _user_id("凭据所属 User 的用户名"),
        R("secret_sha256", description="客户端生成 dbu_ 随机 token 的 SHA256，不提交原文"),
        P("name", description="凭据名称，便于辨认；默认 API"),
        P("expires_in_days", "integer", description="有效期 1-365 天，默认 90"),
    ), (R("credential", "object", "新凭据：credential_id、username、name、created_at、expires_at（Unix 秒）、status，不含密钥"),),
        mutates=True, target="user_id"),
    _d("ListUserAccessTokens", ON_USER, "列出一位 User 的个人访问凭据，不返回密钥。", (
        _user_id(),
    ), (
        R("credentials", "array", "每项 credential_id、name、created_at、expires_at 与 status（active、revoked、expired）"),
    ), target="user_id"),
    _d("RevokeUserAccessToken", FIXED, "撤销当前 User 的一个个人访问凭据。", (
        R("credential_id", description="要撤销的凭据，取 ListUserAccessTokens 返回的 credential_id"),
    ), (R("credential", "object", "撤销后的凭据记录，status 为 revoked"),), mutates=True, target="credential_id"),
    _d("ListUsers", FIXED, "列出调用者可以看到的用户。", (
        P("status", description="按账号状态过滤", enum=("active", "disabled")),
        P("q", description="按用户名或显示名称搜索"),
    ), (R("users", "array", "用户摘要，按用户名不区分大小写排序；每项的 allows.UpdateUser 表示调用者能否修改这个用户"),)),
    _d("GetUser", ON_USER, "读取一个用户的资料与账号状态。", (
        _user_id("用户名；读取自己时也要写明"),
    ), (USER, R("version", "integer", "用户记录的版本（全部用户共用的账号库修订号）；可作 UpdateUser 等的 expected_version")),
       target="user_id"),
    _d("CreateUser", FIXED, "创建一个新的用户账号，并设置初始密码。", (
        _user_id("新账号的用户名"),
        R("initial_password", description="12–256 个字符", max_length=256),
        P("display_name", description="显示名称；省略时用用户名"), P("recovery_email", description="新账号的恢复邮箱；省略时为空"),
    ), (USER, R("personal_ama", "object",
                "新账号的 Ask Me Anything：lineage_id、resident_id；创建失败时只有 error，下次成功登录时内部重试")),
        mutates=True),
    _d("UpdateUser", ON_USER, "修改用户资料、状态与分类。分类是 Portal 管理员（portal_admin）、一般用户（member）或实习用户（intern），"
       "权限方案把各分类的 User 放在 P 树对应的分类节点下。管理员名单不论权限系统开关和方案，只有 Portal 管理员能改："
       "把其他 User 设为或取消 Portal 管理员、停用或启用管理员账号，不能对自己做，并始终保留一位启用中的 Portal 管理员；"
       "一般用户与实习用户之间的调整，权限系统关闭时已登录主体都能做，开启时按方案对本指令的要求判定。", (
        _user_id(),
        R("changes", "object", "display_name、recovery_email、avatar（{sprite_index: 0–41}、CreateUpload purpose=avatar 的 {upload_id}，或 null 恢复默认）、"
          "status（active 或 disabled）、category（" + "、".join(USER_CATEGORIES) + "）"),
    ), (USER, P("category_changed", "boolean", "本次是否改变了分类")), mutates=True, target="user_id"),
    _d("ResetUserPassword", FIXED, "管理员重置他人的密码并撤销其会话；本人修改密码须在 Settings 验证当前密码。", (
        _user_id(), R("new_password", description="12–256 个字符", max_length=256),
    ), (USER,), mutates=True, target="user_id"),
    _d("RevokeUserSessions", FIXED, "撤销指定用户的全部浏览器会话。", (
        _user_id(),
    ), (USER,), mutates=True, target="user_id"),
    _d("DeleteUser", FIXED, "永久删除一个用户账号，并留下不可复用的用户名标记。删除 Portal 管理员等同于取消其管理员身份，只有其他 Portal 管理员能做。", (
        _user_id(),
    ), (R("user_id", description="被删除的用户名"),), mutates=True, target="user_id"),
    _d("GetUserNavigation", ON_USER, "读取一位 User 收藏的 Project、Lineage 和 Resident；收藏不影响导航列出哪些对象。", (
        _user_id(),
    ), (
        R("items", "array", "收藏对象的统一 ID"),
    ), target="user_id"),
    _d("UpdateUserNavigation", ON_USER, "为一位 User 收藏或取消收藏一个 Project、Lineage 或 Resident。", (
        _user_id(),
        R("object_id", description="Project ID，或 Lineage、Resident 的统一 ID"),
        R("favorite", "boolean", "true 收藏，false 取消收藏"),
    ), (R("items", "array", "更新后的收藏对象"),), mutates=True, target="user_id"),
)
