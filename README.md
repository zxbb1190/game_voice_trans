# VoxGo

English documentation: [README_EN.md](README_EN.md)

VoxGo 是一个面向 PC 游戏玩家的开源实时语音翻译浮窗工具，适合海外服开黑、公会语音、Discord 队友沟通和直播字幕辅助。

官网：<https://voxgo.cn/><br>
GitHub：<https://github.com/zxbb1190/VoxGo_game_voice_trans>

## 🎮 功能特性
- **首次启动向导**: 第一次打开先完成翻译接口和音频设备测试，完成后保存 `setup_completed`
- **API Key 测试**: 在向导和设置窗口里真实调用一次翻译接口，确认 Key、模型名和地址可用
- **音频设备测试**: 真实打开当前设备并显示音量条，先确认能检测到游戏/Discord/视频声音
- **实时音频捕获**: 使用 WASAPI Loopback 捕获系统音频输出（不是麦克风）
- **本地语音识别**: 基于 faster-whisper 的离线语音转文字，当前支持固定英语/中文识别
- **中英双向翻译**: 当前正式支持英语 ↔ 中文，在浮窗标题栏选择识别语言和翻译目标语言，并可一键交换方向
- **兼容多种翻译服务**: 支持 OpenAI 兼容 Chat Completions API，也支持 Google Cloud Translation Basic v2
- **游戏浮窗**: 透明置顶窗口，在游戏内显示翻译结果
- **可见状态提示**: 启动状态、音频设备、暂停/恢复、翻译 API 错误码等会直接显示在浮窗里
- **检查更新**: 默认每天检查一次正式版更新，也可以在设置窗口手动检查或忽略某个版本
- **调试与反馈闭环**: 调试模式记录最近一次识别/翻译/浮窗延迟，反馈入口会生成诊断模板
- **手机端同步**: WebSocket 实时推送到手机浏览器
- **全局热键**: 显示/隐藏、清空历史、暂停/恢复，锁定和紧凑模式也可在设置里自定义

## 📁 项目结构
```text
VoxGo_game_voice_trans/
├── main.py              # 兼容启动入口
├── voxgo/               # 应用主包
│   ├── app.py           # VoxGoApp 生命周期协调
│   ├── config/          # 配置结构、加载、迁移和预设
│   ├── audio/           # 音频捕获、设备和分段兼容入口
│   ├── asr/             # Whisper 识别和模型下载入口
│   ├── translation/     # 翻译 Provider 和提示词
│   ├── runtime/         # 运行时事件和工作项
│   ├── ui/              # 浮窗、设置、托盘、二维码入口
│   ├── mobile/          # 手机端服务和静态资源
│   └── update/          # 更新检查入口
├── translator.py        # 翻译兼容入口
├── mobile_server.py     # 手机端服务兼容入口
├── tests/               # 可自动运行的轻量测试
├── diagnostics/         # 手动排查脚本，不参与正常运行
├── config.example.json  # 配置模板
├── config.json          # 本地配置文件（不会提交到 Git）
├── requirements.txt     # Python 依赖
├── install.bat          # 安装脚本
├── run.bat              # 启动脚本
├── assets/voxgo.ico     # Windows 桌面/安装器图标
├── docs/                # 官网页面和品牌图片
├── README_EN.md         # English documentation
└── README.md
```

自动测试可运行：
```bash
python -m unittest discover -s tests
```

手动排查脚本放在 `diagnostics/`，例如依赖导入检查、翻译接口检查、手机二维码生成等。需要真实 API 的脚本会读取本地 `config.json`，不会用于正常启动或打包。

## 🚀 玩家快速开始

### 1. 下载或安装
双击 `install.bat` 或手动运行：
```bash
pip install -r requirements.txt
```

如果使用 Release 里的免安装包，解压后直接运行 `VoxGo.exe`。Lite 包首次运行会下载 Whisper 模型；Full 包已内置 Whisper small 模型，适合网络不稳定的电脑。

### 2. 走完首次启动向导
第一次打开会先显示向导，不会立刻加载 Whisper。按顺序完成：
- 选择翻译服务，填写 API Key、模型名和兼容地址
- 点击“测试 API Key”，确认真实翻译接口能返回结果
- 选择 `[系统声音]` / `Loopback` 音频设备
- 点击“测试音频”，播放游戏、Discord 或视频语音，确认音量条会跳动并显示“检测到声音”
- 点击“完成并启动”，程序会保存 `user_settings.json` 里的 `app.setup_completed=true`，然后开始加载识别模型

点“稍后设置并启动”或直接关闭向导也会继续启动；之后仍可从浮窗右上角齿轮重新测试 API Key 和音频设备。

### 3. 手动配置翻译 API（可选）
首次使用先复制配置模板：
```bash
copy config.example.json config.json
```

然后编辑 `config.json`，或先启动程序后在浮窗右上角齿轮设置里选择翻译服务并填写 API Key。

默认使用 OpenAI 兼容接口：
```json
"translation": {
    "provider": "openai_compatible",
    "api_key": "你的 OpenAI 兼容 API Key",
    "model": "tencent/Hunyuan-MT-7B",
    "endpoint": "https://api.siliconflow.cn/v1/chat/completions"
}
```

模板默认使用硅基流动的 `tencent/Hunyuan-MT-7B`，这是面向翻译的免费模型。只需要注册账号并创建一个 API Key 即可调用；具体免费模型和额度以官网为准。

注册/获取 API Key：<https://cloud.siliconflow.cn/i/iA6DF2nP>

注意：硅基流动账号/额度可能需要支付宝实名或人脸认证；部分海外地区用户可能无法完成该认证。海外用户如果无法开通硅基流动，可以改用 Google Cloud Translation、DeepSeek、Qwen、GLM，或本地模型服务。

也可以使用其他 OpenAI 兼容接口。常见填写示例：

| 来源 | 兼容地址示例 | 模型名示例 |
|------|--------------|------------|
| 硅基流动 | `https://api.siliconflow.cn/v1/chat/completions` | `tencent/Hunyuan-MT-7B` |
| DeepSeek | `https://api.deepseek.com/chat/completions` | `deepseek-v4-flash` |
| Qwen / 阿里云百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions` | `qwen-plus` |
| GLM / 智谱 | `https://open.bigmodel.cn/api/paas/v4/chat/completions` | `glm-4.7` |
| 本地模型 | `http://127.0.0.1:11434/v1/chat/completions` 或 `http://127.0.0.1:8000/v1/chat/completions` | 按本地服务填写 |

如果服务商只给了 `base_url`，例如以 `/v1` 结尾，也可以直接填写；程序会自动补上 `/chat/completions`。真实 API Key 会保存在本机 `user_settings.json`，该文件不会提交到 Git。

也可以使用 Google Cloud Translation：
```json
"translation": {
    "provider": "google",
    "api_key": "你的 Google Cloud Translation API Key",
    "source_lang": "en",
    "target_lang": "zh"
}
```

Google 这里使用的是官方 **Cloud Translation API Basic v2**，不是通用 Google API。需要在 Google Cloud 项目里启用 Cloud Translation API，然后创建 API Key。Google 模式下不需要填写模型名和兼容地址；设置窗口会自动禁用这两个字段。

Google Cloud Translation 价格和免费额度以官方为准：<https://cloud.google.com/translate/pricing>

### 4. 设置音频路由 (可选)
默认会优先使用 Windows WASAPI Loopback 采集当前扬声器/耳机输出，不需要选择麦克风。

在浮窗右上角齿轮设置里可以选择音频设备。优先选择和当前 Windows 输出设备同名的 `[系统声音]` 或带有 `Loopback` 的设备，例如你正在用耳机听游戏，就选对应耳机的系统声音/Loopback 项。

不要选择普通 `[输入设备] 麦克风`。麦克风只能录到环境声，通常录不到游戏声音；如果列表里大部分都是麦克风，说明系统声音捕获设备还没暴露出来。

如果只看到麦克风，可以运行 `install.bat` 更新依赖，或手动安装：
```bash
pip install PyAudioWPatch==0.2.12.8
```

还可以安装 VB-Cable 虚拟音频设备作为备选：
1. 下载：https://vb-audio.com/Cable/
2. 安装后，将系统默认音频输出切换到 VB-Cable
3. 游戏音频将通过 VB-Cable 被捕获

NVIDIA 显卡用户也可以尝试 NVIDIA Broadcast / RTX Voice 作为语音降噪与虚拟音频设备备选：
1. 下载 NVIDIA Broadcast：https://www.nvidia.com/en-us/geforce/broadcasting/broadcast-app/
2. 在 NVIDIA Broadcast / RTX Voice 中选择实际麦克风或扬声器，并开启需要的降噪效果
3. 如果本工具的音频设备列表里出现 NVIDIA Broadcast / RTX Voice 虚拟麦克风或扬声器，可以选择它进行测试

注意：NVIDIA Broadcast / RTX Voice 更适合语音聊天降噪和虚拟麦克风/扬声器场景；采集游戏整体系统声音时，仍优先使用 `[系统声音]` / `Loopback`，或使用 VB-Cable。

### 5. 启动程序
双击 `run.bat` 或运行：
```bash
python main.py
```

进入游戏或语音频道前，先在设置里确认“测试翻译”和“测试音频”都能通过。遇到问题时，打开齿轮设置里的“提交反馈”，复制诊断模板到 GitHub Issue。

## 🎯 使用说明

### 浮窗控制
- **Ctrl+Shift+T**: 切换浮窗显示/隐藏
- **Ctrl+Alt+C**: 清空翻译历史
- **Ctrl+Alt+S**: 暂停/恢复翻译
- **拖拽浮窗**: 可移动位置
- **浮窗按钮**: 可快速切换紧凑浮窗、打开手机二维码、进入设置、退出或锁定浮窗
- **系统托盘**: 可在浮窗被隐藏或游戏遮挡时显示/隐藏浮窗、暂停/恢复、清空字幕、切换紧凑模式、打开设置或退出
- **右上角齿轮**: 配置翻译服务、测试 API Key、测试音频设备、开启调试模式、提交反馈、调整透明度和快捷键

### 翻译方向
浮窗标题栏可以直接选择识别语言和翻译目标语言：
- 左侧下拉框是识别语言
- 右侧下拉框是翻译目标语言
- 中间按钮可以一键交换方向
- 当前正式支持英语和中文双向：英语 → 中文、中文 → 英语
- 中英文混杂内容会按主要语言判断，并尽量保留游戏术语、缩写、人名和地名
- 其它语言暂未作为可选语言开放；即使某些翻译服务商本身支持更多语言，VoxGo 当前界面和识别/翻译流程仍以中英双向为准

### 状态与错误提示
除了正常翻译结果，浮窗也会显示必要的用户提示，例如：
- 启动进度、Whisper 模型加载状态
- lite 包首次下载 Whisper 模型时的模型名、下载来源、已下载大小、总大小和百分比
- 当前选中的系统声音 / Loopback 音频设备
- 音频捕获启动失败或设备枚举失败
- 翻译暂停 / 恢复、清空历史、热键触发
- 翻译 API 超时、服务商 HTTP 状态码和错误信息

### 检查更新
VoxGo 默认每天从官网更新清单检查一次正式版更新。检查只会提示新版本，不会自动下载、安装或替换本地文件。

在浮窗右上角齿轮设置里可以：
- 开启或关闭“每天自动检查”
- 点击“检查更新”手动查询当前版本是否最新
- 发现新版本后打开下载页、稍后提醒，或忽略当前这个版本

自动检查状态、忽略版本和最近检查时间会保存在本机 `user_settings.json`。发布清单位于 `https://voxgo.cn/update.json`，其中下载地址固定到具体 Release tag，避免 `latest` 变化后旧清单指向不匹配的安装包。

### 调试与反馈
设置窗口可以开启“调试模式”，程序会在 `app.log` 里记录最近一次从语音检测、识别、翻译到浮窗更新的延迟。点击“提交反馈”会生成诊断模板，包含版本、系统、音频设备、翻译服务、最近延迟和日志路径，适合直接贴到 GitHub Issue。

### 手机端访问
1. 确保电脑和手机在同一局域网
2. 手机浏览器访问：`http://电脑IP:8765/mobile`
3. 实时接收翻译结果

### 配置调优
编辑 `config.json`：

| 配置项 | 说明 |
|--------|------|
| `app.setup_completed` | 首次启动向导是否已完成；向导完成后会保存到 `user_settings.json` |
| `debug.enabled` | 是否开启调试模式并记录最近一次端到端延迟 |
| `whisper.model_size` | 模型大小: tiny/base/small/medium (越大越准越慢) |
| `whisper.device` | 识别设备，默认 `cpu`，可在齿轮设置的“识别设备”里选择；确认已安装 NVIDIA CUDA 运行环境后可手动选择 `auto` 或 `cuda` |
| `whisper.compute_type` | 计算精度，默认 `auto`：CPU 使用 int8，CUDA 使用 float16 |
| `whisper.cpu_threads` | CPU 加载/识别线程数，默认 2；首次下载模型后内存紧张时保持较小更稳 |
| `whisper.num_workers` | Whisper worker 数，默认 1；调高会增加内存占用 |
| `whisper.model_download_source` | lite 包首次下载 Whisper 模型的下载源：`modelscope` 为 ModelScope 国内源（默认、推荐国内用户），`huggingface` 为官方 Hugging Face，`custom_hf_endpoint` 为自定义 Hugging Face Endpoint |
| `whisper.model_download_endpoint` | 仅 `custom_hf_endpoint` 使用的 Hugging Face 兼容 Endpoint；ModelScope 不是 Hugging Face endpoint，不能填在这里 |
| `whisper.language` | 固定识别语言，会随标题栏左侧语言选择同步；当前可选 `en` / `zh` |
| `whisper.prompt_profile` | 识别提示词场景，默认 `none`，避免 Whisper 幻听提示词；必要时可手动改为 `general` 或 `game` |
| `whisper.vad_filter` | faster-whisper 内部 VAD，默认关闭，避免和外部切段重复吞字 |
| `overlay.position` | 浮窗位置: top/bottom/left/right |
| `overlay.text_color` | 文字颜色 (十六进制) |
| `overlay.bg_color` | 浮窗背景色，默认深灰 `#20242A` |
| `overlay.bg_opacity` | 浮窗背景透明度，默认 0.82；设置窗口里可调整 |
| `audio.latency_mode` | 响应模式：`fast` 极速、`balanced` 均衡（默认）、`accurate` 准确、`custom` 自定义；齿轮设置里可直接选择 |
| `audio.sample_rate` | 采样率 (推荐 16000) |
| `audio.chunk_duration_ms` | 自定义模式下的音频块长度，均衡默认 220ms；越小响应越快但更容易切碎 |
| `audio.silence_threshold` | 静态兜底语音阈值，单位 dBFS；默认 -40，真人语音不建议高于 -20 |
| `audio.speech_threshold_blocks` | 自定义模式下连续多少个有声块后判定开始说话，均衡默认 2 |
| `audio.silence_limit_blocks` | 自定义模式下连续多少个静音块后切段，均衡默认 4 |
| `audio.speech_idle_timeout_ms` | 有语音缓冲但没有新音频帧时的主动切段时间，均衡默认 650ms |
| `audio.pre_roll_ms` | 触发说话前保留的句首缓冲，均衡默认 450ms |
| `audio.soft_silence_margin_db` | 当前片段峰值下降多少 dB 后按尾部静音处理，默认 10 |
| `audio.soft_silence_gate_margin_db` | 音量接近语音门限时按尾部静音处理的余量，默认 5 |
| `audio.noise_calibration_seconds` | 启动后采集背景噪声并自动校准阈值的秒数，默认 2 |
| `audio.noise_margin_db` | 动态阈值相对背景噪声提高的 dB，默认 7 |
| `audio.max_speech_seconds` | 连续有声时强制切段的最长秒数，均衡默认 6 秒 |
| `audio.min_segment_seconds` | 识别前过滤低于该语音活跃时长的片段，均衡默认 0.35 秒；设为 0 可关闭 |
| `audio.min_segment_peak_margin_db` | 识别前要求片段峰值至少高过当前语音门限的 dB，均衡默认 1.5；设为 0 可关闭 |
| `translation.provider` | 翻译服务：`openai_compatible` 或 `google` |
| `translation.api_key` | 当前翻译服务的 API Key；Google 模式下填写 Google Cloud Translation API Key |
| `translation.model` | OpenAI 兼容模型名，默认 `tencent/Hunyuan-MT-7B`；Google 模式不用填写 |
| `translation.endpoint` | OpenAI 兼容地址，可填 `/v1` base_url 或完整 `/chat/completions`；Google 模式不用填写 |
| `translation.max_tokens` | 译文最大输出长度，默认 80，避免模型扩写 |
| `translation.temperature` | 翻译随机性，默认 0，优先忠实稳定 |
| `translation.source_lang` | 固定识别语言，标题栏左侧下拉框会保存到这里；当前可选 `en` / `zh` |
| `translation.target_lang` | 固定翻译目标语言，标题栏右侧下拉框会保存到这里；当前可选 `en` / `zh` |
| `translation.context_messages` | 翻译历史上下文条数，默认 0，避免历史污染和补全 |
| `translation.timeout_seconds` | 单次翻译请求超时时间，默认 12 秒；服务商较慢时可适当调高 |
| `translation.max_concurrent_requests` | 同时进行的翻译请求数，默认 2；调低更稳，调高可能让慢服务商更容易超时 |

## 🔧 故障排除

### 1. 无法捕获音频
- 优先选择 `[系统声音]` / `Loopback` 设备，不要选普通麦克风
- 先在首次向导或齿轮设置里点击“测试音频”，播放游戏、Discord 或视频声音，看音量条是否跳动
- 运行 `python diagnostics/list_devices.py`，确认能看到系统声音设备
- 确保游戏声音正在从你选择的扬声器/耳机播放
- 如果你正在用蓝牙耳机/HDMI 显示器/USB 声卡输出，音频设备也要选择同名的系统声音/Loopback 项
- 如果只看到麦克风，重新运行 `install.bat` 安装 PyAudioWPatch
- 如果使用 VB-Cable，确保系统音频输出已切换到 VB-Cable
- NVIDIA 显卡用户可尝试 NVIDIA Broadcast / RTX Voice 虚拟设备，但采集游戏整体系统声音仍优先选择 `[系统声音]` / `Loopback`
- 尝试以管理员身份运行

### 2. 语音识别不准确
- 真人语音比视频声音更容易偏小，确认 `audio.silence_threshold` 不要设得过高；推荐 -40 左右
- 启动后先保持 2 秒相对安静，让程序完成背景噪声校准
- 调高 `whisper.model_size` (small → medium)
- 默认保持 `whisper.prompt_profile=none`；如果出现“请准确转写...”这类文本，说明 Whisper 正在幻听提示词，不要开启长提示词
- 如果句子开头或结尾容易丢字，先保持 `whisper.vad_filter=false`，避免双重 VAD 切音
- 降低游戏内背景音乐音量
- 确保游戏语音输出清晰，且选择的是正在播放游戏声音的系统声音设备
- 如果口音重、句子较长或直播/会议场景优先完整度，在齿轮设置里把“响应模式”切到“准确”

### 3. 启动时提示 cublas64_12.dll 缺失
- 这是 CUDA/cuBLAS 运行库缺失，不是翻译 API 问题
- 默认配置已使用 CPU 识别，不需要安装 CUDA
- 在齿轮设置里把“识别设备”改成 `CPU（推荐）`，然后重启程序
- 只有确认电脑有 NVIDIA 显卡，并且安装了匹配 faster-whisper/ctranslate2 的 CUDA 12 与 cuDNN 运行库后，才建议开启 `cuda`

### 4. 独占全屏游戏里看不到浮窗
- 优先把游戏显示模式改成“无边框窗口”或“窗口化全屏”；独占全屏可能会压住所有桌面浮窗
- 先在桌面把浮窗拖到合适位置，再点击锁定，减少鼠标干扰
- 使用系统托盘或热键控制：显示/隐藏、暂停/恢复、清空字幕都不需要打开设置窗口
- 如果游戏以管理员身份运行，VoxGo 也建议以管理员身份启动，否则热键和置顶可能不稳定
- 设置窗口“说明”页里有全屏兼容和系统托盘说明；“快捷键”页可查看和调整当前快捷键

### 5. lite 包首次下载模型很慢或失败
- 浮窗会显示正在下载的 Whisper 模型、仓库、下载来源、已下载大小、总大小和百分比
- 下载失败时会显示具体网络错误，并写入程序目录的 `app.log` 和 `crash_report.txt`
- 默认下载源是 ModelScope 国内源，会从 `modelscope.cn` 拉取 `Systran/faster-whisper-small` 的必要文件
- `hf-mirror.com` 目前会跳转回 `huggingface.co`，用户网络访问不了 Hugging Face 时不可靠；如需尝试，只能在“自定义 Hugging Face Endpoint”里填写
- 如果 ModelScope 或自定义源仍失败，可以切到官方 Hugging Face 后重启，或直接下载 full 包
- full 包已内置 Whisper small 模型，不需要首次下载模型

### 6. 翻译延迟高
- 在齿轮设置里开启调试模式，复现一次后通过“提交反馈”复制最近延迟
- 在齿轮设置里把“响应模式”切到“极速”或“均衡”；极速适合 PUBG、APEX、Valorant 等竞技场景，必要时可把 `whisper.model_size` 改为 `base`
- 检查网络连接
- 降低 `whisper.model_size` 以加快识别
- 使用本地翻译模型 (需自行部署)

### 7. 手机端无法连接
- 检查防火墙是否放行 8765 端口
- 确认手机和电脑在同一局域网
- 尝试使用电脑 IP 而非 localhost
- 如果看到 502，先在电脑浏览器打开 `http://127.0.0.1:8765/mobile`；如果本机可打开，通常是手机访问的 IP、代理或防火墙问题
- 手机应访问浮窗二维码/启动提示里的电脑地址，不要手动使用浏览器代理给出的公网或代理地址

## 📊 性能指标

| 组件 | 延迟 | 资源占用 |
|------|------|----------|
| 音频捕获 | < 50ms | 低 |
| Whisper-small | 1-2s | CPU: 中等 / GPU: 低 |
| OpenAI 兼容 API | 0.5-1.5s | 网络依赖 |
| 浮窗渲染 | < 10ms | 低 |

## 🎮 适用范围
本工具通过 Windows 系统声音设备采集音频，不针对某一款游戏做专门适配。只要游戏语音能从当前扬声器、耳机、HDMI、USB 声卡或虚拟声卡正常播放，并且系统能暴露对应的 `[系统声音]` / `Loopback` 设备，通常就可以尝试使用。

未逐个实测的游戏不应标记为“已兼容”。如果某些游戏、反作弊、独占音频模式、远程串流、DRM 保护或特殊声卡驱动阻止系统声音被捕获，需要改用其他输出设备、关闭独占模式，或使用 VB-Cable 等虚拟音频设备绕路。

## 📝 注意事项
1. 首次运行会下载 Whisper 模型（默认 base 约 150MB；更大模型会占用更多空间）
2. 需要稳定的网络连接用于翻译 API
3. 建议在游戏前启动本程序
4. 手机端需要保持浏览器页面打开

## 🤝 贡献
欢迎提交 Issue 和 Pull Request 改进项目！

项目仓库：<https://github.com/zxbb1190/VoxGo_game_voice_trans>

## 📄 许可证
本社区版项目采用 GNU General Public License v3.0，详见 [LICENSE](LICENSE)。

如需闭源商业使用、私有定制发行或商业版授权，请另行取得授权。
