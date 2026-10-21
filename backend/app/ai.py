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
