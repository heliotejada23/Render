from fastapi import FastAPI, Request
import requests
import os
import whisper
import tempfile

# Inicializamos la app de FastAPI
app = FastAPI()

# Configura tus variables de entorno en Render:
# TELEGRAM_TOKEN
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Carga el modelo de Whisper local (usa uno pequeño para Render Free)
print("🔄 Cargando modelo Whisper local...")
model = whisper.load_model("base")  # también puedes usar "tiny" o "small"
print("✅ Modelo cargado correctamente.")

@app.get("/")
async def root():
    return {"message": "🤖 Bot de Telegram con Whisper local activo!"}

@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        print("📩 Nuevo mensaje recibido:", data)

        if "message" not in data:
            return {"ok": True}

        message = data["message"]
        chat_id = message["chat"]["id"]

        # Si hay nota de voz
        if "voice" in message:
            file_id = message["voice"]["file_id"]

            # 1️⃣ Obtener el archivo de Telegram
            file_info = requests.get(f"{TELEGRAM_API}/getFile?file_id={file_id}").json()
            file_path = file_info["result"]["file_path"]
            file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"

            # 2️⃣ Descargar audio en archivo temporal
            response = requests.get(file_url)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as tmp_file:
                tmp_file.write(response.content)
                audio_path = tmp_file.name

            # 3️⃣ Transcribir usando Whisper local
            print("🎧 Transcribiendo con Whisper local...")
            result = model.transcribe(audio_path, language="es")
            texto = result["text"].strip()
            print(f"✅ Transcripción: {texto}")

            # 4️⃣ Enviar respuesta a Telegram
            requests.post(f"{TELEGRAM_API}/sendMessage", json={
                "chat_id": chat_id,
                "text": f"🎙️ Esto fue lo que entendí:\n\n{texto}"
            })

        else:
            # Si no hay audio
            requests.post(f"{TELEGRAM_API}/sendMessage", json={
                "chat_id": chat_id,
                "text": "👋 Envíame una nota de voz y la transcribiré con Whisper local."
            })

        return {"ok": True}

    except Exception as e:
        print("⚠️ Error en webhook:", e)
        return {"ok": False, "error": str(e)}
