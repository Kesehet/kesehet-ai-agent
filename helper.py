from flask import request


def get_request_json():
	return request.get_json(silent=True) or {}


def to_jsonable(value):
	if isinstance(value, dict):
		return {
			key: to_jsonable(item)
			for key, item in value.items()
		}

	if isinstance(value, list):
		return [to_jsonable(item) for item in value]

	if isinstance(value, tuple):
		return [to_jsonable(item) for item in value]

	if hasattr(value, "model_dump"):
		return to_jsonable(value.model_dump())

	if isinstance(value, (str, int, float, bool)) or value is None:
		return value

	return str(value)


def response_text(ai_responded):
	ai_responded = to_jsonable(ai_responded)

	if isinstance(ai_responded, dict):
		content = ai_responded.get("content")
		if content is not None:
			return content

		response = ai_responded.get("response")
		if isinstance(response, dict):
			message = response.get("message", {})
			if isinstance(message, dict):
				return message.get("content", "")

		return str(response or "")

	return str(ai_responded or "")


def response_messages(ai_responded, fallback_messages):
	if isinstance(ai_responded, dict):
		return to_jsonable(ai_responded.get("messages", fallback_messages))

	return to_jsonable(fallback_messages)
