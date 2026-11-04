# 可选 Resident 云语音工具

此目录是按部署需要选装的 Resident 工具，不是 Portal 内置能力、Provider 或 HATG 指令。Portal 启动、身份入口和核心测试不导入本目录、不安装此依赖、不探测网络。无安装/网络不可用只影响主动调用这个工具，不影响普通对话和已有媒体阅读。

部署者可将该脚本作为 Resident 的命令行工具使用，或用部署现有工具注册方式暴露；不自动注册全站能力。

```sh
python3 -m venv /chosen/tools/edge-speech
/chosen/tools/edge-speech/bin/pip install -r optional_tools/cloud_speech/requirements.txt
/chosen/tools/edge-speech/bin/python optional_tools/cloud_speech/edge_speech.py --check
/chosen/tools/edge-speech/bin/python optional_tools/cloud_speech/edge_speech.py --text-file narration.txt --output-dir new-audio-run --voice zh-CN-YunxiNeural --boundary WordBoundary
```

只有显式最后一步会访问微软云语音，失败不自动重试；目录必须是新的，防止覆盖旧音轨。没有私有凭据、付费API登记或本地合成回退。--check不访问云服务。

speech.mp3与receipt.json记录实际Microsoft/edge-tts版本、voice、语言、文本/音频SHA、真实边界和严格原文位置。模型名edge-neural-tts是引擎别名。记录是工具产生的来源材料，不是Portal服务签发证明或微软签名。可信程度依赖实际工具执行记录与独立审查，作者JSON单独不能证明音色或调用来源。

边界100ns tick及秒值来自SDK，包含SDK音频分块补偿；原文位置为转义前Unicode码点半开区间，严格匹配失败则null，不按字数插值。已有句级音轨不能反推词级时序。保留按真实音轨、停顿、字幕与响度检查的制作要求。

CLI默认WordBoundary，也可选SentenceBoundary；一次调用只请求一种粒度。时间从源MP3起点计，offset_seconds/duration_seconds分别等于offset_ticks/duration_ticks除以10,000,000。input_start/input_end是原始UTF-8文件解码后的零基Unicode码点索引，左闭右开（Python直接切片，JS先Array.from）；不是UTF-8字节、UTF-16索引或XML转义后的偏移。SDK将不兼容控制字符等长替换为空格，事件text已反转义一次，不再二次反转义。仅按事件顺序严格匹配并跳过空白/标点，失败后不重新猜测锚点；真实时间和text仍保留。alignment中的source_audio_sha256与input_sha256绑定本次文件；分享边界text时应与音频、讲稿采用相同的资源读取边界。

解释页可使用portal-audio-provenance/v1索引：provider/model为真实来源，request_id为工具run_id，audio为同包音频引用，evidence为同包receipt.json引用。Portal核验资源权限、内容摘要与记录一致性，不把工具JSON升级成服务端生成认证。
