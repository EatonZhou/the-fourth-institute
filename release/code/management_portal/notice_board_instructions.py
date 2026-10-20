"""Notice Board instruction declarations (decision_3e063aac15b3), executed by Portal Web.

The Notice Board's part of the instruction catalog (``isa.instructions`` in
``portal_features.py``); every service reads it, Portal Web runs the
handlers in ``notice_board_web_isa.py``.  A Portal without the Notice Board
has none of these instructions.
"""
from __future__ import annotations

from management_portal.hatg.declarations import FIXED, InstructionDeclaration as D, On, P, R

CATEGORIES = {"discussion": "公共讨论"}


def _d(name, decides, description, parameters=(), result=(), *, mutates=False, target=""):
    return D(name=name, category="discussion", owner="web", description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates, target=target,
             decides=decides)


ON_POST = (On("post_id", "notice"),)


POST_CATEGORIES = ("bug", "request", "release", "chat", "other")
POST = R("post", "object", "帖子全文：post_id、标题、分类、作者、正文、置顶与精华状态、notification_revision 与全部回复")

DECLARATIONS = (
    _d("ListPosts", FIXED, "列出帖子摘要，并标明调用者尚未读到的帖子。", (
        P("category", description="只列出这一分类的帖子；省略时列出全部", enum=POST_CATEGORIES),
    ), (
        R("posts", "array", "帖子摘要，置顶、精华在前，再按最近活动排序；每项 post_id、title、category、author、reply_count、"
          "pinned、featured、unread、notification_revision 等，不含正文"),
        R("post_count", "integer", "列出的帖子数"), R("featured_count", "integer", "其中精华帖数"),
        R("unread_count", "integer", "其中调用者未读的帖子数"),
    )),
    _d("GetPost", ON_POST, "读取一篇帖子、它的全部回复和调用者可执行的操作。", (R("post_id", description="要读取的帖子"),), (
        POST, R("replies", "array", "全部回复：reply_id、author、content、created_at、floor、can_delete"),
        R("notification_revision", "integer", "帖子的通知版本，每有回复加一；作 MarkPostRead 的读到版本，也是帖子的 expected_version"),
        R("permissions", "object", "调用者可执行的操作：can_edit、can_delete"),
    ), target="post_id"),
    _d("CreatePost", FIXED, "发布一篇新帖子，作者的已读位置同时设为当前版本。", (
        R("title", description="帖子标题", max_length=120),
        R("category", description="分类：bug Bug、request 需求、release Portal Release News、chat 闲谈、other 其他",
          enum=POST_CATEGORIES),
        R("content", description="帖子正文", max_length=30000),
    ), (POST,), mutates=True),
    _d("UpdatePost", ON_POST, "由作者修改帖子的标题、分类或正文。", (
        R("post_id", description="要修改的帖子"), R("changes", "object", "title、category、content"),
    ), (POST,), mutates=True, target="post_id"),
    _d("DeletePost", ON_POST, "由作者删除帖子，同时删除其全部回复和已读记录。", (R("post_id", description="要删除的帖子"),),
       (R("post_id", description="被删除的帖子"),), mutates=True, target="post_id"),
    _d("CreateReply", ON_POST, "回复一篇帖子，帖子的通知版本加一。", (
        R("post_id", description="要回复的帖子"), R("content", description="回复正文", max_length=8000),
    ), (R("reply", "object", "新回复：reply_id、author、content、created_at"),
        R("notification_revision", "integer", "回复后帖子的通知版本")), mutates=True, target="post_id"),
    _d("DeleteReply", FIXED, "由回复作者删除自己的回复。", (
        R("post_id", description="回复所在的帖子"), R("reply_id", description="要删除的回复"),
    ), (POST,), mutates=True, target="post_id"),
    _d("PinPost", FIXED, "设置或取消帖子的置顶。", (
        R("post_id", description="要置顶或取消置顶的帖子"), R("pinned", "boolean", "true 置顶，false 取消置顶"),
    ), (POST,), mutates=True, target="post_id"),
    _d("FeaturePost", FIXED, "设置或取消帖子的精华标记。", (
        R("post_id", description="要设置精华的帖子"), R("featured", "boolean", "true 设为精华，false 取消精华"),
    ), (POST,), mutates=True, target="post_id"),
    _d("MarkPostRead", ON_POST, "把帖子标为读到指定版本，或重新标为未读。", (
        R("post_id", description="要标记的帖子"),
        P("notification_revision", "integer", "读到的版本，取 GetPost 返回的 notification_revision；省略时为当前版本",
          minimum=0),
        P("unread", "boolean", "true 时退回到最新回复之前、重新标为未读，忽略 notification_revision"),
    ), (R("read_revision", "integer", "调用者在这篇帖子上保存的已读版本"),
        R("unread_count", "integer", "调用者在全部帖子中仍未读的帖子数")), mutates=True, target="post_id"),
)
