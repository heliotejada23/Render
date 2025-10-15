import os
import json
import requests
from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime, timedelta
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

# -------------------------------------------------------------
# CONFIGURACIÓN
# -------------------------------------------------------------
TELEGRAM_API = os.getenv("TELEGRAM_API")
HF_TOKEN = os.getenv("HUGGINGFACE_TOKEN")
HF_MODEL_URL = "https://api-inference.huggingface.co/models/openai/whisper-large-v3-turbo"

# Token de Google Calendar (puede venir del entorno o archivo)
GOOGLE_TOKEN = os.getenv("GOOGLE_TOKEN_JSON")
if GOOGLE_TOKEN:
    creds = Credentials.from_authorized_user_info(json.loads(GOOGLE_TOKEN))
else:
    with open("token.json", "r") as f:
        creds = Credentials.from_authorized_user_info(json.load(f))

calendar_service = build("calendar", "v3", credentials=creds)

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
    """Descarga un archivo de voz desde Telegram"""
    file_info_url = f"https://api.telegram.org/bot{TELEGRAM_API}/getFile?file_id={file_id}"
    file_info = requests.get(file_info_url).json()

    if not file_info.get("ok") or "result" not in file_info:
        raise Exception(f"Error obteniendo archivo Telegram: {file_info}")

    file_path = file_info["result"]["file_path"]
    file_url = f"https://api.telegram.org/file/bot{TELEGRAM_API}/{file_path}"
    response = requests.get(file_url)

    if response.status_code != 200:
        raise Exception(f"Error al descargar audio: {response.status_code}")

    return response.content

def transcribe_audio(audio_data: bytes):
    """Envía audio a Whisper (Hugging Face) y devuelve el texto"""
    response = requests.post(
        HF_MODEL_URL,
        headers={
            "Authorization": f"Bearer {HF_TOKEN}",
            "Content-Type": "audio/ogg"
        },
        data=audio_data
    )

    if response.status_code != 200:
        raise Exception(f"Error desde Hugging Face: {response.text}")

    result = response.json()
    text = result.get("text")
    if not text and isinstance(result, list) and len(result) > 0 and "text" in result[0]:
        text = result[0]["text"]
    return text or ""

def detect_event_info(text: str):
    """Detecta si el texto incluye un evento o recordatorio simple"""
    text_lower = text.lower()
    event = None

    # Ejemplos básicos de detección
    if "reunión" in text_lower or "cita" in text_lower or "recordatorio" in text_lower:
        start_time = datetime.utcnow() + timedelta(minutes=1)
        end_time = start_time + timedelta(minutes=30)
        event = {
            "summary": text.capitalize(),
            "start": {"dateTime": start_time.isoformat() + "Z"},
            "end": {"dateTime": end_time.isoformat() + "Z"},
        }
    return event

def create_calendar_event(event):
    """Crea un evento en Google Calendar"""
    try:
        created = calendar_service.events().insert(calendarId="primary", body=event).execute()
        print(f"✅ Evento creado: {created.get('htmlLink')}")
        return created.get("htmlLink")
    except Exception as e:
        print(f"⚠️ Error al crear evento: {e}")
        return None

# -------------------------------------------------------------
# MODELOS
# -------------------------------------------------------------
class TelegramUpdate(BaseModel):
    update_id: int
    message: dict

# -------------------------------------------------------------
# WEBHOOK DE TELEGRAM
# -------------------------------------------------------------
@app.post("/webhook")
async def telegram_webhook(update: TelegramUpdate):
    message = update.message
    chat_id = message["chat"]["id"]
    print(f"📩 Mensaje recibido: {message}")

    if "text" in message:
        text = message["text"]
        send_message(chat_id, f"📝 Has dicho: {text}")

        # Detectar comandos de evento
        event = detect_event_info(text)
        if event:
            link = create_calendar_event(event)
            if link:
                send_message(chat_id, f"📅 Evento creado correctamente.\n🔗 {link}")
            else:
                send_message(chat_id, "⚠️ No pude crear el evento en el calendario.")
        return {"ok": True}

    elif "voice" in message:
        voice = message["voice"]
        file_id = voice["file_id"]
        duration = voice.get("duration", 0)

        if duration > 10:
            send_message(chat_id, "🎧 Audio largo, procesando... dame unos segundos ⏳")
        else:
            send_message(chat_id, "🎧 Transcribiendo tu nota de voz...")

        try:
            audio_data = download_file(file_id)
            text = transcribe_audio(audio_data)

            if text:
                send_message(chat_id, f"🎙️ Texto detectado: {text}")
                event = detect_event_info(text)
                if event:
                    link = create_calendar_event(event)
                    if link:
                        send_message(chat_id, f"📅 Evento añadido al calendario:\n🔗 {link}")
                    else:
                        send_message(chat_id, "⚠️ No pude añadir el evento al calendario.")
            else:
                send_message(chat_id, "⚠️ No pude extraer texto del audio.")
        except Exception as e:
            print(f"❌ Error procesando audio: {e}")
            send_message(chat_id, f"❌ Error al procesar el audio: {e}")

    return {"ok": True}

# -------------------------------------------------------------
# RUTA PRINCIPAL
# -------------------------------------------------------------
@app.get("/")
def root():
    return {"status": "ok", "message": "🤖 Bot Telegram + Whisper + Google Calendar activo 🚀"}
