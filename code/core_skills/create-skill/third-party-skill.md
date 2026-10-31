# Third-party Skill 规范

## 位置

Third-party skill 放在：

```text
${share_root}/Skills/third-party/<skill-name>/SKILL.md
```

## 适用场景

用于保存外部迁入或第三方风格的 skill。

## 创建要求

- 尽量保留第三方原文语义。
- TFI Portal canonical 规则不要只写在 third-party skill 中。
- 需要本地化时，另建 TFI Portal shared skill，或在正文明确 TFI Portal 适配边界。
- 若第三方内容很长，入口 `SKILL.md` 只保留使用路线，详细规范拆到与 `SKILL.md` 并列的文件。
