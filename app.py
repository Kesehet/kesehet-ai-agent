from pathlib import Path

from flask import Flask, abort, jsonify, render_template, send_from_directory
from ai import ai_response, select_tasks_for_prompt, get_starter_context, select_tools_for_prompt
from helper import get_request_json, response_messages, response_text
from core.db import init_db
from core.scheduler_runner import get_scheduler_status, start_scheduler_runner


INTERNAL_ROOT = Path(__file__).resolve().parent / "internal"


# Basic Flask application
app = Flask(__name__, static_folder="static", static_url_path="/static")
init_db()
_services_started = False


@app.before_request
def ensure_background_services():
	global _services_started
	if not _services_started:
		start_scheduler_runner()
		_services_started = True

@app.route('/')
def index():
	return render_template("index.html")


@app.route('/heartbeat')
def heartbeat():
	return jsonify({"alive": True})


@app.route('/scheduler/status')
def scheduler_status():
	return jsonify(get_scheduler_status())


@app.route('/internal_file/<path:file_path>')
def internal_file(file_path):
	root = INTERNAL_ROOT.resolve()
	target = (root / file_path).resolve()
	if target != root and root not in target.parents:
		abort(404)
	if not target.is_file():
		abort(404)
	return send_from_directory(root, target.relative_to(root))


@app.route('/plan', methods=['POST'])
def agent():
	# Get the prompt from the request
	data = get_request_json()
	prompt = data.get('prompt', '')
	tasks = select_tasks_for_prompt(prompt)
	messages = get_starter_context(prompt)
	return jsonify({"tasks": tasks, "messages": messages})
	
@app.route('/run_task', methods=['POST'])
def run_task():
	# Get the task from the request
	data = get_request_json()
	task = data.get('task', '')
	messages = data.get('messages') or []
	# Run the task
	ai_responded = ai_response(task, messages=messages)
	return jsonify({
		"response": response_text(ai_responded),
		"messages": response_messages(ai_responded, messages),
	})

@app.route('/summarize', methods=['POST'])
def summarize():
	# Get the messages from the request
	data = get_request_json()
	messages = data.get('messages') or []
	# Summarize the messages
	ai_responded = ai_response("Summarize the conversation without mentioning the internal workings. Only respond to the initial user message.", messages=messages)

	return jsonify({
		"summary": response_text(ai_responded),
		"messages": response_messages(ai_responded, messages),
	})

if __name__ == '__main__':
	# Run in debug mode for development. In production, use a proper WSGI server.
	app.run(host='0.0.0.0', port=5000, debug=True)
