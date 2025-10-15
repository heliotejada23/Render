from fastapi import FastAPI, Request
import requests
import os
from openai import OpenAI

# Inicializamos la app de FastAPI
app = FastAPI()

# Configura tus variables de entorno en Render:
# TELEGRAM_TOKEN y OPENAI_API_KEY
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Cliente moderno de OpenAI (nuevo SDK)
client = OpenAI(api_key=OPENAI_API_KEY)

# API base de Telegram
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Endpoint raíz opcional (para evitar error 404 al abrir la URL)
@app.get("/")
async def root():
    return {"message": "🤖 Bot de Telegram con Whisper y FastAPI activo!"}

# Endpoint principal del Webhook
@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        print("📩 Nuevo mensaje recibido:", data)  # Log útil en Render

        if "message" not in data:
            return {"ok": True}

        message = data["message"]
        chat_id = message["chat"]["id"]

        # Si el mensaje tiene audio o nota de voz
        if "voice" in message:
            file_id = message["voice"]["file_id"]

            # 1️⃣ Obtener la ruta del archivo desde Telegram
            file_info = requests.get(f"{TELEGRAM_API}/getFile?file_id={file_id}").json()
            file_path = file_info["result"]["file_path"]
            file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"

            # 2️⃣ Descargar el audio
            audio_data = requests.get(file_url).content
            audio_path = "audio.ogg"
            with open(audio_path, "wb") as f:
                f.write(audio_data)

            # 3️⃣ Transcribir el audio con Whisper
            with open(audio_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file
                )

            texto = transcript.text
            print(f"🎧 Transcripción: {texto}")

            # 4️⃣ Responder al usuario con el texto
            requests.post(f"{TELEGRAM_API}/sendMessage", json={
                "chat_id": chat_id,
                "text": f"🎙️ Esto fue lo que entendí:\n\n{texto}"
            })

        else:
            # Si el usuario no envía audio
            requests.post(f"{TELEGRAM_API}/sendMessage", json={
                "chat_id": chat_id,
                "text": "👋 Envíame una nota de voz y la transcribiré con Whisper."
            })

        return {"ok": True}

    except Exception as e:
        print("⚠️ Error en webhook:", e)
        return {"ok": False, "error": str(e)}
