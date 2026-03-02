import asyncio
import io
import json
import os
import ssl

import websockets
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file

load_dotenv()

app = Flask(__name__)

MODEL = "speech-2.8-hd"
FILE_FORMAT = "mp3"
DEFAULT_VOICE_ID = "moss_audio_0251081c-f530-11f0-8583-3ae0c9a1b09a"


async def _tts_to_bytes(api_key: str, text: str, voice_id: str) -> bytes:
    url = "wss://api.minimax.io/ws/v1/t2a_v2"
    headers = {"Authorization": f"Bearer {api_key}"}

    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    ws = await websockets.connect(url, additional_headers=headers, ssl=ssl_context)
    try:
        connected = json.loads(await ws.recv())
        if connected.get("event") != "connected_success":
            raise RuntimeError("WebSocket connection failed")

        start_msg = {
            "event": "task_start",
            "model": MODEL,
            "voice_setting": {
                "voice_id": voice_id,
                "speed": 1,
                "vol": 1,
                "pitch": 0,
                "english_normalization": False,
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": FILE_FORMAT,
                "channel": 1,
            },
        }
        await ws.send(json.dumps(start_msg))
        response = json.loads(await ws.recv())
        if response.get("event") != "task_started":
            raise RuntimeError("Task start failed")

        await ws.send(json.dumps({"event": "task_continue", "text": text}))

        audio_data = b""
        while True:
            response = json.loads(await ws.recv())
            if "data" in response and response["data"].get("audio"):
                audio_data += bytes.fromhex(response["data"]["audio"])
            if response.get("is_final"):
                break

        return audio_data
    finally:
        try:
            await ws.send(json.dumps({"event": "task_finish"}))
            await ws.close()
        except Exception:
            pass


@app.route("/")
def index():
    return render_template("index.html", default_voice_id=DEFAULT_VOICE_ID)


@app.route("/tts", methods=["POST"])
def tts():
    data = request.get_json(force=True)
    text = (data.get("text") or "").strip()
    voice_id = (data.get("voice_id") or DEFAULT_VOICE_ID).strip() or DEFAULT_VOICE_ID

    if not text:
        return jsonify({"error": "文本不能为空"}), 400

    api_key = os.getenv("MINIMAX_API_KEY")
    if not api_key:
        return jsonify({"error": "服务端未配置 MINIMAX_API_KEY"}), 500

    try:
        audio_bytes = asyncio.run(_tts_to_bytes(api_key, text, voice_id))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return send_file(
        io.BytesIO(audio_bytes),
        mimetype="audio/mpeg",
        as_attachment=False,
        download_name="output.mp3",
    )


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
