# 声明式计算画面合同

`portal-explanation-video/v1` 保留已有音轨、来源证明、字幕与持久媒体接口。新增 `scene.visual`，画面通过封闭的数学操作生成；不接受脚本、表达式或作者声称的计算结果。计算图、公式、对照和测试使用同一个数值实现。

## 场景和事件

每个场景仍有 title、speech、start、end。最多24个场景，总时长仍不超过300秒；可选的 duration 估时字段为2–60秒，实际媒体边界使用 start/end。纯 visual 计划可省 nodes，不能在同一个视觉场景混用旧 edges/values。现存节点图计划与旧 ready 结果保留原行为。

`visual = {kind, condition, data, events}`：
- kind 仅允许下表八种操作。
- condition 是常驻的条件说明，最多80字符；应写当前查询、模式和固定条件，条件重置必须明示。
- data 是各操作规定的数据结构。所有数学结果由后端计算。
- events 最多48项，绝对时间严格递增且动画不重叠。事件为 `{at, stage, index, duration, cue}`：at 是音轨绝对秒；duration 为0–3秒；index 为当前对象的0起始索引；cue 是根 cues 的索引。事件 onset 必须位于该真实字幕段内。cue:null 表示明确静默停读，校验拒绝其时间范围与任何字幕相交。
- 事件之前只保留标题与条件；最后事件结束后稳定停留，不按时钟循环切换高亮。
- `duration` 仅用于缩放和平移的连续画面，其余阶段为精确时间切换。作者可以在一条字幕内用多个显式事件安排分量展开。静默保持无需重复事件。
- 音轨与字幕时间的真实性仍由已有正式语音来源准入及实际制作回读负责。数字时间在合法区间，并不能证明旁白已正确对齐。

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

含 visual 的片段为1280×720、12fps；底部80px为真实字幕保留区。显示数值取四位近似，内部计算保留完整精度。源字图和数学结果每场景准备一次；每帧仅查当前事件和绘制该阶段。混合旧图与新图时统一输出尺寸。

渲染器不读取来源、调用模型、执行作者脚本或提交检查。原有 media/audio/review/http owner继续负责鉴权、固定哈希、正式语音来源、租约、持久结果与独立送检。本次没有放宽任何准入；缺少合规音轨仍无法生产新片。

## 作者接入

[14镜数据形状示例](/r/workspace_9a9d56f3d1af/file/explanation-pages-evidence/backend/visual-v3/estimated-preview-plan.json)展示各操作的组合。它使用分镜估时，**没有音轨、正式语音证明或真实cues，不能直接提交为生产视频**。该示例仅用于理解字段，服务端不加载作者适配脚本。

正式音轨就绪后，逐句校准 scenes.start/end 与 events.at，填写对应真实cue索引；留白时间显式cue:null。实际时长超过300秒时按语义拆片或重录。固定manifest与plan的来源和SHA沿既有接口更新，新固定版单独生成；不要改写已保存的视频。


## 正式云语音与原始音轨

先经 `ListSpeechProviders` 发现服务，再用 `GenerateSpeech` 的稳定幂等键提交一次旁白，使用 `GetSpeechGeneration` 回读 completed 回执。微软云服务当前使用 `api_id=microsoft-edge-tts`、`model=edge-neural-tts`，后者为服务引擎别名；记录实际 voice，不冒称厂商披露的模型版本。交付未知时不自动重放请求。

计划 audio 接受同包原始 MP3 或 PCM16 WAV。`audio_provenance` 为同包固定JSON引用，正文 schema=`portal-native-audio-provenance/v1`，字段 provider、model、request_id、audio、evidence；request_id填实际Portal execution ID，audio与计划完全一致，evidence指向保存的正式回执JSON。服务端另行查询执行服务的权威完成记录，以真实actor验证原上传权限、音频SHA与provider/model/MIME，再验证包内副本权限及字节。作者保存的JSON只作索引，不能自行证明生成发生。

原MP3字节复制保留原SHA。受管ffmpeg在生成目录中解码成PCM16/24kHz单声道，固定demuxer与file/pipe协议，不执行作者命令；解码最多30秒，实际音轨超过300秒直接拒绝。生成记录保存source_sha256、source_media_type及实际解码时长，派生WAV不得冒用原MP3摘要。旧ready成果仍按原合同读取，不补写新来源证明。

