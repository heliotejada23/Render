from fastapi import FastAPI, Request
import requests
import openai
import os

app = FastAPI()

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")  # lo tomará de las variables de Render
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    message = data.get("message", {})

    # Si el usuario envía un audio (nota de voz)
    if "voice" in message:
        chat_id = message["chat"]["id"]
        file_id = message["voice"]["file_id"]

        # Descarga del archivo de Telegram
        file_info = requests.get(f"{TELEGRAM_API}/getFile?file_id={file_id}").json()
        file_path = file_info["result"]["file_path"]
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"

        # Guardar el audio temporalmente
        audio_data = requests.get(file_url).content
        with open("audio.ogg", "wb") as f:
            f.write(audio_data)

        # Transcripción con Whisper
        with open("audio.ogg", "rb") as audio_file:
            transcript = openai.Audio.transcriptions.create(
                model="whisper-1",
                file=audio_file
            )
        text = transcript.text

        # Respuesta al usuario
        requests.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": f"🎙️ Entendí: {text}"
        })

    else:
        # Si no hay audio, solo responde un mensaje base
        chat_id = message.get("chat", {}).get("id")
        if chat_id:
            requests.post(f"{TELEGRAM_API}/sendMessage", json={
                "chat_id": chat_id,
                "text": "Envíame un audio y lo transcribiré 🎧"
            })

    return {"ok": True}
