GPT-SoVITS 参考音频放这里（本机路径，给 SoVITS 服务读）：

  ref.wav          必填，3～10 秒干净人声
  ref.txt          可选，与 ref.wav 同内容的文本；也可只用环境变量 QQBOT_SOVITS_PROMPT_TEXT

先在本机启动 GPT-SoVITS API，例如：
  python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml

插件默认请求：http://host.docker.internal:9880/tts
参考音路径默认：本目录下的 ref.wav（可用 QQBOT_SOVITS_REF_AUDIO 改成 SoVITS 进程能访问的路径）
