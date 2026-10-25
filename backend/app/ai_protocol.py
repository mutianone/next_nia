"""Provider-specific structured response options shared by business requests."""
def structured_options(provider: str, model: str) -> dict:
    options = {"response_format": {"type": "json_object"}, "stream": False}
    if provider == "deepseek":
        options["thinking"] = {"type": "disabled"}
    elif "qwen3" in model.lower() and "thinking" not in model.lower():
        options["enable_thinking"] = False
    return options
