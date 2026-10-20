"""Self-description instructions (decision_aa4fdbac8364)."""
from __future__ import annotations

from ..declarations import FIXED, Fixed, InstructionDeclaration as D, On, P, R


DECLARATIONS = (
    # The catalog lists the installed categories as the enum of ``category``.
    D(name="ListInstructions", category="isa", owner="entry", decides=FIXED,
      description="列出已登记的指令。",
      parameters=(P("category", description="只列出这一类别的指令；省略时列出全部"),),
      result=(R("instructions", "array", "指令名、类别、拥有服务、说明、是否改变状态和是否流式；"
                "RunResourceAction 下列出各资源类型的动作"),
              R("guide", "string", "Agent 指引的地址（Portal 内路径），按任务说明身份、调用方式和常用指令"))),
    D(name="GetInstructionSchema", category="isa", owner="entry", decides=FIXED,
      description="读取一条指令的完整定义。",
      parameters=(R("instruction", description="要读取定义的指令名，例如 GetResident"),),
      result=(R("schema", "object", "指令完整定义：名称、类别、拥有服务、说明、parameters、result、target、one_of 等，"
                "以及当前权限方案下的调用要求 permission"),)),
    D(name="GetInstructionExecution", category="isa", owner="entry",
      description="读取一次指令执行的状态和业务引用；可用执行 ID，或当前调用者的原指令名与幂等键定位。查询不重执行；未找到不代表未受理。"
                  "属于 Lineage 的回执按回执保存的 Lineage 判定（via: instruction_execution.lineage，默认方案要求 @viewer），"
                  "调用者作用域的回执只对原调用者开放。",
      decides=(On("instruction_id", via="instruction_execution.lineage", optional=True), Fixed("@portal")),
      parameters=(P("instruction_id", description="与 instruction+idempotency_key 互斥"),
                  P("instruction", description="原状态变更指令名；与 idempotency_key 一起提供"),
                  P("idempotency_key", description="当前调用者提交原指令时的幂等键，不查其他调用者", max_length=240)),
      one_of=(("instruction_id",), ("instruction", "idempotency_key")),
      result=(R("instruction_id", description="执行 ID"), R("instruction", description="原指令名"),
              R("status", description="executing 执行中、committed 已完成或 failed 失败"),
              R("references", "object", "从结果中保留的业务对象引用：ID、版本与布尔标志"),
              P("error", "object", "失败时的错误：code、message、retryable、commit_state、details"),
              R("created_at", description="受理时间"), R("updated_at", description="状态最近变化的时间"))),
)
