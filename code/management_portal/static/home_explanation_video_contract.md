# 声明式计算画面合同

`portal-explanation-video/v1` 使用固定音轨、字幕与持久媒体接口。新增 `scene.visual`，画面通过封闭的数学操作生成；不接受脚本、表达式或作者声称的计算结果。计算图、公式、对照和测试使用同一个数值实现。

## 场景和事件

每个场景仍有 title、speech、start、end。最多24个场景，总时长仍不超过300秒；可选的 duration 估时字段为2–60秒，实际媒体边界使用 start/end。纯 visual 计划可省 nodes，不能在同一个视觉场景混用旧 edges/values。现存节点图计划与旧 ready 结果保留原行为。

`visual = {kind, condition, data, events}`：
- kind 仅允许下表八种操作。
- condition 是常驻的条件说明，最多80字符；应写当前查询、模式和固定条件，条件重置必须明示。
- data 是各操作规定的数据结构。所有数学结果由后端计算。
- events 最多48项，绝对时间严格递增且动画不重叠。事件为 `{at, stage, index, duration, cue}`：at 是音轨绝对秒；duration 为0–3秒；index 为当前对象的0起始索引；cue 是根 cues 的索引。事件 onset 必须位于该真实字幕段内。cue:null 表示明确静默停读，校验拒绝其时间范围与任何字幕相交。
- 事件之前只保留标题与条件；最后事件结束后稳定停留，不按时钟循环切换高亮。
- `duration` 仅用于缩放和平移的连续画面，其余阶段为精确时间切换。作者可以在一条字幕内用多个显式事件安排分量展开。静默保持无需重复事件。
- 音轨与字幕时间的真实性由作者制作和独立成片检查负责。数字时间在合法区间，并不能证明旁白已正确对齐。

对象列表最多6项；每项共用稳定 `id`（<=32字符）与 `label`（<=16字符）。单场景不得重名；跨镜头应沿用同一来源ID。数值向量目前为2维，分量范围[-100,100]，均须有限数；高维机制先选取明确的2维教学切片。不要把任意高维数据悄悄裁成2维。

## 操作数据

| kind | data | stage |
| --- | --- | --- |
| dot | query_label, query:[x,y], items:[{id,label,vector:[x,y]}], divisor(0.1..100) | vectors / products / sum / result / scores |
| normalize | items:[{id,label,score(-50..50),masked:boolean}], temperature(0.1..10) | scores / mask / exp / denominator / weights |
| weighted_sum | items:[{id,label,vector:[x,y],weight(0..1)}]；权重和1 | vector / scaled / accumulate / result |
| compare | items:[{id,label,key:[x,y],vector:[x,y]}], divisor, before, after, change | before / change / after |
| matrix | rows:[{id,label,vector:[x,y]}], items:[{id,label,key:[x,y],vector:[x,y]}], divisor, causal:boolean | row / all / result |
| projection | input_label, input:[1..6个系数], lanes:[{id,label,matrix:[[x,y],…]}] | input / multiply / result |
| pipeline | input_label,input_width,lane_width,lanes:[{id,label,detail}],merge_label,projection_label,output_label,context:[文字] | lanes / concat / project / residual / context |
| sequence | items:[{id,label,detail}], note | focus |

- dot：逐分量相乘，相加后除 divisor；所有向量共用实际长度与坐标尺度。scores 显示完整带零线的正负分数行，不把分数称作概率。
- normalize：mask先进入score，排除项指数为0；其余做 exp(score/T)，共同分母求和后相除。极端数值先减共同偏移以避免溢出，画面明确该偏移。exp(0)=1。weights阶段按index逐项填入同一长度为1的预算条。
- weighted_sum：每一维贡献等于weight×vector；用完整精度累加部分和。vector保留原向量，scaled显示同尺度缩小，accumulate只平移当前贡献；平移未落定不把端点称为部分和。零贡献保留为静止点。result才画最终直达输出箭头。
- compare：before/after都是 `{label,query:[x,y],masked:[逐项布尔],temperature}`。change为query、masked或temperature之一，校验要求其余条件相等，K/V在两侧共享。change阶段隐藏尚未落定的权重，after重新计算共同分母和输出。两侧条形共用0–1尺度。
- matrix：每行以rows中的vector查询共同keys，行归一化后乘values；causal=true时要求行列数一致，并使列索引大于行索引的权重为0。画面圆面积代表权重，遮线表示不可见，右侧展开当前行数值。result展示逐行输出与矩阵维数。
- projection：每个输入系数乘矩阵对应行，再求和输出2维。最多三条独立通道；矩阵行数必须等于输入维数。参数和结果分别显示。
- pipeline：最多三条符号通道、五个背景模块。每头lane_width维；Concat沿特征维拼为lane_count×lane_width，再经输出矩阵恢复input_width，与同维输入做残差。这里只给出维数，不虚构未定义的多头参数。
- sequence：受限的来源/步骤索引与说明，作为引入和收束；核心计算须使用上述数学操作，不能用它替代计算。

## 输出和资源边界

含 visual 的片段为1280×720；low为12fps，medium/high为24fps；底部80px为真实字幕保留区。显示数值取四位近似，内部计算保留完整精度。源字图和数学结果每场景准备一次；每帧仅查当前事件和绘制该阶段。混合旧图与新图时统一输出尺寸。

渲染器不读取来源、调用模型、执行作者脚本或提交检查。媒体与检查入口负责鉴权、固定哈希、租约、持久结果与独立送检。音轨由作者在外部准备，缺少音轨只影响需要它的新视频任务。

## 作者接入

### 质量与固定版本

manifest 的 `quality` 为 low/medium/high，构建脚本以 `--quality` 固定它。low解释包只接受Markdown supplement；medium沿用交互网页；high通过统一审核入口送审。视频按钮可另选视频质量，POST `/api/home-explanation/media` 与GET状态/文件携带相同quality。medium目录沿用 `explanation-media/<manifest_sha>`，low/high分别为 `<manifest_sha>-low`、`<manifest_sha>-high`；不同质量不复用ready结果。已有固定成果可读，GET不会生成、通知或投递。

`home_explanation_quality.prepare_plan(validated_plan, quality)` 是解释视频的生成门槛。low允许既有节点图；medium/high要求每个场景都有已通过封闭校验、绑定真实字幕cue或明确静默的visual.events，避免自动循环高亮代替分镜。按钮不会在渲染器内调用模型重写分镜：缺少合格计划时，明确POST通过同owner的 `AuthoringService.submit(subject, actor)` 投递正式负责Resident，payload携带实际 `policy(quality)` 和修改分镜、固定新版本、回原域生成的要求。作者必须实际制作计划与音轨；submitted仅是创作请求回执，界面显示awaiting_authoring，不能显示ready。帧率和编码差异只表示渲染策略，不证明3b1b教学质量；high仍需要独立实际检查。

AuthoringService与ReviewService同在 `home_explanation_review.py`，使用独立的quality-authoring任务记录，绝不充当另一套审核事实。`read(id, actor)` 和GET `/api/home-explanation/authoring?id=...`只读；`cancel(id, actor)`仅原请求者可调用。作者开始和发布新产物前读取任务状态，已取消不得继续。固定同一请求重复POST不重复投递，只有投递失败可显式重试且最多三次。报告域必须消费相同策略门槛或正式作者任务入口；无法做到时明确保留未完成，不能用同模板加quality元数据冒充执行了更高质量创作。

新medium/high视频即使原计划已有合法visual.events，也必须经过上述正式作者任务。解释页负责Resident修改实际计划后，以本人凭据POST `/api/home-explanation/media/authoring` `{address,sha256,quality,authoring_id}` 回填新固定包；服务核验任务状态、本人身份、档位、原固定源和新计划语义，单改标题、JSON排版或质量标签不能通过。`build_explanation_package.py --authoring-id`在保存新manifest后调用同一入口。通过后持久authoring_proof，再明确POST生成；跨质量切换不能复用证明。旧ready视频继续只读恢复，不重投作者任务、不重生成。报告通过subject.task的presentation/UpdateResource合同完成相同的实际作者修改检查，不要求解释页的visual结构。

### 单一审核事实

报告、页面和视频共用 `home_explanation_review.ReviewService`。域在明确POST完成固定产物后调用 `submit(subject, actor, reviewer='')`；subject为 `{kind, ref, version, digest, quality:'high', author, responsible_resident_id?, assets:[{address,sha256}], task?, previous_review_id?}`，kind为report/report_video/explanation/explanation_video。ref/version/digest/quality/author共同固定审核身份，独立于作者的responsible_resident_id也进入身份。author保持实际创建者；作者为Resident时可作为负责者，作者为User时必须由业务域核验正式assistant消息关联并持久记录responsible_resident_id，不能从客户端任意指定或猜测其AMA。缺少关联保存awaiting_author，不能仅通知User声称完成Resident修改闭环。审核者同时与作者、请求者、负责Resident分离，使用自身权限读取并实际播放/操作产物。AuthoringService使用同一subject字段，但quality允许medium/high。

负责者关联只决定创作与反馈投递对象，不授予资源写权限。创作任务和审核反馈都要求负责Resident先以本人身份CheckPermission，再使用正式UpdateResource或原域更新入口；没有实际写权限时在正式作者回复中保留具体blocked原因并请求授权，不携带或借用请求者User凭据。submitted和feedback_delivery.delivered仅为传输事实，不能当成实际修改、质量通过或闭环完成。业务域仍须在实际修改时检查当前actor的写权限。

读回 `read(review_id, actor)`；结论 `verdict(review_id, actor, digest=..., status=passed|changes_requested|blocked, evidence=[{address,sha256}], feedback=...)`；取消 `cancel(review_id, actor)`。HTTP对应GET `/api/home-explanation/reviews?id=...`、POST `/reviews/verdict`和`/reviews/cancel`；页面送审POST `/reviews`使用固定manifest地址及sha256。CLI `submit_explanation_review.py` 支持 `--read`、`--read-authoring`、`--cancel`、`--verdict-file`及 `--previous-review-id`，身份来自当前Resident凭据；页面API的X-Portal-Lineage-ID由当前Resident的正式GetResident回读取得，不能自报别人的Lineage。

submitted仅表示DeliverMessage受理，ready仅表示媒体字节可用；high的quality_met只有固定摘要仍一致且passed才成立。passed须有实际检查证据，作者不能自审。changes_requested保存意见并通过幂等DeliverMessage回作者，feedback_delivery记录真实投递成功或失败。作者被唤醒后修改实际产物、保存新固定版，再携原review_id为previous_review_id送审；新摘要不继承旧pass。实际修改与独立复审不限制轮次，第4轮及以后仍可送检；仅passed、显式取消或具体无法继续的blocked停止。传输失败的送检或结果通知最多三次显式重试。取消、权限失败、生成失败、通知失败均保留可读状态，绝不自动降级为通过；GET不重投或重试。固定资产字节被改写时，原passed读回为invalidated。

[14镜数据形状示例](/r/workspace_9a9d56f3d1af/file/explanation-pages-evidence/backend/visual-v3/estimated-preview-plan.json)展示各操作的组合。它使用分镜估时，**没有音轨或真实cues，不能直接提交为生产视频**。该示例仅用于理解字段，服务端不加载作者适配脚本。

正式音轨就绪后，逐句校准 scenes.start/end 与 events.at，填写对应真实cue索引；留白时间显式cue:null。实际时长超过300秒时按语义拆片或重录。固定manifest与plan的来源和SHA沿既有接口更新，新固定版单独生成；不要改写已保存的视频。

需要句内揭示时，按固定音轨的实际内容与时间安排动作。没有足够对齐依据时保留未对齐，不按字数插值。不同计算阶段应给读者足够时间区分；时间戳合法不等于教学节奏合适，需在成片正常速度播放中检查。


## 固定音轨输入

作者提交固定音轨文件和对应的真实字幕时间，文件使用普通资源地址与内容摘要引用。

计划audio接受同包固定MP3或PCM16 WAV的address/sha256。每次媒体请求使用当前主体重新检查来源读取权限；生成前及写入成果前核对音轨字节摘要，撤销权限或内容变化不能发布成果。计划顶层允许附带创作注释，渲染器只接收已验证的定义字段，不解释、读取或执行这些注释；场景和visual仍遵守封闭字段校验。

原MP3字节复制保留原SHA。受管ffmpeg在生成目录中解码成PCM16/24kHz单声道，固定demuxer与file/pipe协议，不执行作者命令；解码最多30秒，实际音轨超过300秒直接拒绝。生成记录保存source_sha256、source_media_type及实际解码时长，派生WAV不得冒用原MP3摘要。已有音轨、创作附件与ready视频保持原件，按普通资源权限读取。
