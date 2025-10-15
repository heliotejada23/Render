from fastapi import FastAPI, Request
import requests
import os
import tempfile
import threading

# --- Configuración general ---
app = FastAPI()

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Token de Hugging Face
HF_TOKEN = os.getenv("HUGGINGFACE_TOKEN")

# URL del modelo gratuito de Whisper
HF_MODEL_URL = "https://api-inference.huggingface.co/models/openai/whisper-tiny.en"


# --- Función auxiliar para transcribir usando Hugging Face ---
def transcribir_audio(chat_id, file_url):
    try:
        # 1️⃣ Descargar el archivo de audio de Telegram
        file_response = requests.get(file_url)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as tmp_file:
            tmp_file.write(file_response.content)
            audio_path = tmp_file.name

        # 2️⃣ Enviar el archivo a Hugging Face
        print(f"🎧 Enviando audio de chat {chat_id} a Hugging Face...")
        with open(audio_path, "rb") as audio_file:
            response = requests.post(
                HF_MODEL_URL,
                headers={"Authorization": f"Bearer {HF_TOKEN}"},
                data=audio_file.read()
            )

        # 3️⃣ Procesar respuesta
        if response.status_code == 200:
            result = response.json()
            texto = result.get("text", "").strip()
            if texto:
                print(f"✅ Transcripción lista: {texto}")
                respuesta = f"🎙️ Esto fue lo que entendí:\n\n{texto}"
            else:
                print("⚠️ Hugging Face no devolvió texto.")
                respuesta = "❌ No pude transcribir el audio."
        else:
            print("⚠️ Error desde Hugging Face:", response.text)
            respuesta = "⚠️ Error al procesar el audio."

        # 4️⃣ Enviar respuesta a Telegram
        requests.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": respuesta
        })

    except Exception as e:
        print("⚠️ Error al transcribir:", e)
        requests.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": "❌ Ocurrió un error durante la transcripción."
        })

# --- Endpoint raíz ---
@app.get("/")
async def root():
    return {"message": "🤖 Bot de Telegram con Whisper (Hugging Face API)"}

# --- Webhook principal de Telegram ---
@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        print("📩 Mensaje recibido:", data)

        if "message" not in data:
            return {"ok": True}

        message = data["message"]
        chat_id = message["chat"]["id"]

        # Si hay nota de voz
        if "voice" in message:
            file_id = message["voice"]["file_id"]
            duration = message["voice"].get("duration", 0)

            # Mensaje inicial rápido
            texto_inicial = (
                "🎧 Audio largo recibido, procesando... Esto puede tardar unos segundos ⏳"
                if duration > 8 else
                "🎧 Procesando tu nota de voz..."
            )

            requests.post(f"{TELEGRAM_API}/sendMessage", json={
                "chat_id": chat_id,
                "text": texto_inicial
            })

            # Obtener la URL real del archivo
            file_info = requests.get(f"{TELEGRAM_API}/getFile?file_id={file_id}").json()
            file_path = file_info["result"]["file_path"]
            file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"

            # Procesar el audio en un hilo aparte
            threading.Thread(target=transcribir_audio, args=(chat_id, file_url)).start()

        else:
            # Si no hay audio
            requests.post(f"{TELEGRAM_API}/sendMessage", json={
                "chat_id": chat_id,
                "text": "👋 Envíame una nota de voz y la transcribiré con Whisper."
            })

        return {"ok": True}

    except Exception as e:
        print("⚠️ Error general en webhook:", e)
        return {"ok": False, "error": str(e)}

