from fastapi import FastAPI, Request
import requests
import os
import whisper
import tempfile
import threading
import time

# Inicializamos la app
app = FastAPI()

# Configura tu token de Telegram como variable de entorno
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Carga el modelo local de Whisper (tiny/base/small según recursos)
print("🔄 Cargando modelo Whisper local...")
model = whisper.load_model("tiny")  # usa "base" si tienes más CPU disponible
print("✅ Modelo Whisper cargado correctamente.")

@app.get("/")
async def root():
    return {"message": "🤖 Bot de Telegram con Whisper local activo y optimizado"}

# Función auxiliar para transcribir en segundo plano
def procesar_audio(chat_id, file_url, message_id=None):
    try:
        # Descargar el audio
        response = requests.get(file_url)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as tmp_file:
            tmp_file.write(response.content)
            audio_path = tmp_file.name

        # Transcribir
        print(f"🎧 Transcribiendo audio para chat {chat_id}...")
        start = time.time()
        result = model.transcribe(audio_path, language="es")
        end = time.time()

        texto = result["text"].strip()
        print(f"✅ Transcripción lista ({round(end-start,1)}s): {texto}")

        # Enviar la respuesta final
        requests.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": f"🎙️ Esto fue lo que entendí:\n\n{texto}"
        })

    except Exception as e:
        print("⚠️ Error durante la transcripción:", e)
        requests.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": "❌ Ocurrió un error al procesar el audio."
        })

@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        print("📩 Nuevo mensaje recibido:", data)

        if "message" not in data:
            return {"ok": True}

        message = data["message"]
        chat_id = message["chat"]["id"]

        # Si el mensaje tiene nota de voz
        if "voice" in message:
            file_id = message["voice"]["file_id"]
            duration = message["voice"].get("duration", 0)

            # Enviar respuesta rápida (Telegram espera una respuesta inmediata)
            if duration > 8:
                texto_espera = "🎧 Audio largo recibido, procesando... Esto puede tardar unos segundos ⏳"
            else:
                texto_espera = "🎧 Procesando tu nota de voz..."

            requests.post(f"{TELEGRAM_API}/sendMessage", json={
                "chat_id": chat_id,
                "text": texto_espera
            })

            # Obtener la URL del archivo
            file_info = requests.get(f"{TELEGRAM_API}/getFile?file_id={file_id}").json()
            file_path = file_info["result"]["file_path"]
            file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"

            # Procesar el audio en un hilo separado
            hilo = threading.Thread(target=procesar_audio, args=(chat_id, file_url))
            hilo.start()

        else:
            # Mensaje sin audio
            requests.post(f"{TELEGRAM_API}/sendMessage", json={
                "chat_id": chat_id,
                "text": "👋 Envíame una nota de voz y la transcribiré con Whisper local."
            })

        return {"ok": True}

    except Exception as e:
        print("⚠️ Error en webhook:", e)
        return {"ok": False, "error": str(e)}
