from time import monotonic

import httpx

from .schemas import AITestRequest, Settings, reveal

PROVIDER_URLS = {
    "siliconflow": "https://api.siliconflow.cn/v1/models",
    "deepseek": "https://api.deepseek.com/models",
}


class AIConnectionError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


def test_ai_connection(request: AITestRequest, settings: Settings) -> dict:
    configured = getattr(settings.ai.providers, request.provider)
    api_key = reveal(request.api_key) or reveal(configured.api_key)
    model = request.chat_model or configured.chat_model
    if not api_key:
        raise AIConnectionError(400, "请先填写 API 密钥，或保存密钥后再测试。")
    if len(api_key) > 4096 or any(character in api_key for character in "\r\n\x00"):
        raise AIConnectionError(400, "API 密钥格式无效，请重新填写。")
    if request.capability != "models":
        return test_ai_capability(request, settings, api_key, model)
    started = monotonic()
    try:
        # No redirects, ambient proxy settings, model invocation or business payload.
        with httpx.Client(timeout=httpx.Timeout(12.0, connect=5.0), follow_redirects=False, trust_env=False) as client:
            response = client.get(PROVIDER_URLS[request.provider], headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"})
        if response.status_code in (401, 403):
            raise AIConnectionError(400, "服务商未接受此密钥，请检查密钥状态和接口权限。")
        if response.status_code == 429:
            raise AIConnectionError(429, "服务商请求频率受限，请稍后再试。")
        if response.status_code != 200:
            raise AIConnectionError(502, "服务商连接检查失败，请稍后重试或检查服务状态。")
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise AIConnectionError(502, "服务商返回的模型列表格式异常。")
        models = sorted({
            item["id"] for item in payload["data"]
            if isinstance(item, dict) and isinstance(item.get("id"), str)
            and 0 < len(item["id"]) <= 200 and api_key not in item["id"]
            and not any(character in item["id"] for character in "\r\n\x00")
        })
        if model not in models:
            raise AIConnectionError(400, "密钥可连接，但所选模型不在当前可用模型列表中，请核对模型名称。")
        return {"ok": True, "message": "凭证与模型列表检查通过；尚未发送对话或业务资料。", "models": models, "latency_ms": round((monotonic() - started) * 1000)}
    except AIConnectionError:
        raise
    except httpx.TimeoutException:
        raise AIConnectionError(504, "连接服务商超时，请检查网络出口后重试。") from None
    except (httpx.HTTPError, ValueError, TypeError):
        raise AIConnectionError(502, "无法连接服务商或解析响应，请检查网络和服务商状态。") from None


def test_ai_capability(request: AITestRequest, settings: Settings, key: str, model: str) -> dict:
    """Exercise the actual business adapters, with fixed non-personal inputs only."""
    from pathlib import Path
    from pydantic import SecretStr
    from .records_ai import analyze_segments, transcribe_audio, RecordAIError
    if request.capability == "asr" and request.provider != "siliconflow":
        raise AIConnectionError(422, "语音转写仅支持硅基流动。")
    configured = settings.model_copy(deep=True)
    configured.ai.enabled = True
    configured.ai.default_provider = request.provider
    selected = getattr(configured.ai.providers, request.provider)
    selected.enabled, selected.api_key, selected.chat_model = True, SecretStr(key), model
    started = monotonic()
    try:
        if request.capability == "asr":
            selected.asr_model = request.asr_model or selected.asr_model
            result = transcribe_audio((Path(__file__).resolve().parents[1] / "assets" / "healthcheck.mp3").read_bytes(), "mp3", "audio/mpeg", configured)
            # Successful HTTP alone is not an intelligible transcription.
            if "测试" not in result["text"] or "会议" not in result["text"]:
                raise AIConnectionError(502, "语音接口已响应，但标准测试录音的关键词未识别完整，请核对语音模型。")
            message = "实际语音转写通过，已识别测试录音关键词。"
        else:
            result = analyze_segments([{"segment_id": "connection-check", "text": "工作会议安排在今天下午三点，地点为二楼会议室。"}], configured)
            if not result["key_facts"]:
                raise AIConnectionError(502, "模型已响应，但未提取测试事实；请检查模型的结构化输出能力。")
            message = "实际生成通过：摘要、结构化要素和来源引用校验成功。"
        return {"ok": True, "message": message, "capability": request.capability, "provider": result["provider"], "model": result["model"], "latency_ms": round((monotonic() - started) * 1000)}
    except RecordAIError as error:
        raise AIConnectionError(error.status, error.message) from None
    except OSError:
        raise AIConnectionError(500, "本地测试音频读取失败，请检查安装文件。") from None
