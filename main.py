import os
import requests
from fastapi import FastAPI, Request
from pydantic import BaseModel

# -------------------------------------------------------------
# CONFIGURACIÓN
# -------------------------------------------------------------
TELEGRAM_API = os.getenv("TELEGRAM_API")
HF_TOKEN = os.getenv("HUGGINGFACE_TOKEN")  # Tu token de Hugging Face
HF_MODEL_URL = "https://api-inference.huggingface.co/models/openai/whisper-large-v3-turbo"

app = FastAPI()

# -------------------------------------------------------------
# FUNCIONES AUXILIARES
# -------------------------------------------------------------
def send_message(chat_id: int, text: str):
    """Envía un mensaje de texto a Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_API}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    requests.post(url, json=payload)

def download_file(file_id: str):
    """Descarga un archivo de Telegram de manera segura"""
    file_info_url = f"https://api.telegram.org/bot{TELEGRAM_API}/getFile?file_id={file_id}"
    file_info = requests.get(file_info_url).json()

    if not file_info.get("ok") or "result" not in file_info:
        print(f"⚠️ Error al obtener archivo de Telegram: {file_info}")
        raise Exception("No se pudo obtener la información del archivo desde Telegram")

    file_path = file_info["result"]["file_path"]
    file_url = f"https://api.telegram.org/file/bot{TELEGRAM_API}/{file_path}"
    response = requests.get(file_url)

    if response.status_code != 200:
        print(f"⚠️ Error al descargar archivo desde Telegram: {response.status_code}")
        raise Exception("No se pudo descargar el archivo de Telegram")

    return response.content


# -------------------------------------------------------------
# ESTRUCTURA DE DATOS
# -------------------------------------------------------------
class TelegramUpdate(BaseModel):
    update_id: int
    message: dict

# -------------------------------------------------------------
# WEBHOOK TELEGRAM
# -------------------------------------------------------------
@app.post("/webhook")
async def telegram_webhook(update: TelegramUpdate):
    message = update.message
    chat_id = message["chat"]["id"]

    print(f"📩 Mensaje recibido: {message}")

    # Si es texto normal
    if "text" in message:
        send_message(chat_id, f"👋 Recibí tu mensaje: {message['text']}")
        return {"ok": True}

    # Si es un mensaje de voz
    elif "voice" in message:
        voice = message["voice"]
        file_id = voice["file_id"]
        duration = voice.get("duration", 0)

        # Si el audio es largo, avisamos antes de procesarlo
        if duration > 10:
            send_message(chat_id, "🎧 El audio es largo, dame unos segundos para transcribirlo...")
        else:
            send_message(chat_id, "🎧 Transcribiendo tu nota de voz...")

        try:
            # Descargamos el archivo desde Telegram
            audio_data = download_file(file_id)

            print(f"🎧 Enviando audio de chat {chat_id} a Hugging Face...")

            # Enviamos el audio a la API de Hugging Face
            response = requests.post(
                HF_MODEL_URL,
                headers={"Authorization": f"Bearer {HF_TOKEN}"},
                files={"file": ("audio.ogg", audio_data, "audio/ogg")}
            )

            # Procesamos la respuesta
            if response.status_code == 200:
                result = response.json()

                # El resultado puede tener diferentes estructuras según el modelo
                text = result.get("text")
                if not text and isinstance(result, list) and len(result) > 0 and "text" in result[0]:
                    text = result[0]["text"]

                language = result[0].get("language", "desconocido") if isinstance(result, list) else "desconocido"

                if text:
                    print(f"✅ Transcripción lista: {text}")
                    send_message(chat_id, f"🎙️ Texto: {text}\n🌍 Idioma detectado: {language.capitalize()}")
                else:
                    print(f"⚠️ Respuesta sin texto: {result}")
                    send_message(chat_id, "⚠️ No pude extraer el texto del audio.")
            else:
                print(f"⚠️ Error desde Hugging Face: {response.text}")
                send_message(chat_id, "⚠️ Ocurrió un error al procesar el audio. Intenta de nuevo más tarde.")

        except Exception as e:
            print(f"❌ Error procesando audio: {e}")
            send_message(chat_id, "❌ Error interno al procesar el audio.")

    return {"ok": True}

# -------------------------------------------------------------
# RUTA PRINCIPAL (saludo)
# -------------------------------------------------------------
@app.get("/")
def root():
    return {"status": "ok", "message": "Bot de Telegram + Whisper + Hugging Face activo 🚀"}

