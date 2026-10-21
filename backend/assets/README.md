# 本地询问音频素材

这四段 WAV 与 interviews.json 为演示资料生成器配套素材。对话由项目编写，使用 macOS 自带 Tingting 中文声音本地合成，并转换为 16 kHz 单声道 PCM WAV。不是真实询问或真人录音，也未发送给外部服务。人物和单位均为虚构。运行时只需读取文件，不依赖 macOS、语音服务或 FFmpeg。

interviews.json 保存与每个文件一致的对话文字及角色。生成器根据 WAV 实际帧数记录时长，不伪造逐字时间轴。
