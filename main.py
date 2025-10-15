import os
import json
import requests
from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime, timedelta
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
import dateparser

# -------------------------------------------------------------
# CONFIGURACIÓN
# -------------------------------------------------------------
TELEGRAM_API = os.getenv("TELEGRAM_API")
HF_TOKEN = os.getenv("HUGGINGFACE_TOKEN")
HF_MODEL_URL = "https://api-inference.huggingface.co/models/openai/whisper-large-v3-turbo"

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

# -------------------------------------------------------------
# INTELIGENCIA DE FECHAS Y TAREAS
# -------------------------------------------------------------
def parse_datetime_from_text(text: str):
    """Detecta fecha y hora en lenguaje natural (en español)"""
    parsed_date = dateparser.parse(
        text,
        languages=["es"],
        settings={"PREFER_DATES_FROM": "future"}
    )
    return parsed_date

def detect_event_type(text: str):
    """Determina si es evento o tarea"""
    t = text.lower()
    if any(w in t for w in ["reunión", "cita", "evento", "llamada", "videollamada", "entrega"]):
        return "evento"
    elif any(w in t for w in ["recordatorio", "tarea", "hacer", "pendiente"]):
        return "tarea"
    return "evento"  # Por defecto

def create_calendar_event(summary, start_time):
    """Crea un evento en Google Calendar"""
    try:
        end_time = start_time + timedelta(minutes=30)
        event = {
            "summary": summary.capitalize(),
            "start": {"dateTime": start_time.isoformat(), "timeZone": "Europe/Madrid"},
            "end": {"dateTime": end_time.isoformat(), "timeZone": "Europe/Madrid"},
        }
        created = calendar_service.events().insert(calendarId="primary", body=event).execute()
        print(f"✅ Evento creado: {created.get('htmlLink')}")
        return created.get("htmlLink")
    except Exception as e:
        print(f"⚠️ Error al crear evento: {e}")
        return None

def create_calendar_task(summary, due_time):
    """Crea una tarea como evento de día completo"""
    try:
        event = {
            "summary": f"Tarea: {summary.capitalize()}",
            "start": {"date": due_time.date().isoformat()},
            "end": {"date": (due_time.date() + timedelta(days=1)).isoformat()},
        }
        created = calendar_service.events().insert(calendarId="primary", body=event).execute()
        print(f"✅ Tarea creada: {created.get('htmlLink')}")
        return created.get("htmlLink")
    except Exception as e:
        print(f"⚠️ Error al crear tarea: {e}")
        return None

# -------------------------------------------------------------
# ESTRUCTURA TELEGRAM
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

    # --- Mensaje de texto ---
    if "text" in message:
        text = message["text"]
        send_message(chat_id, f"📝 Entendido: {text}")

        event_type = detect_event_type(text)
        parsed_time = parse_datetime_from_text(text)

        if parsed_time:
            if event_type == "evento":
                link = create_calendar_event(text, parsed_time)
                send_message(chat_id, f"📅 Evento creado: {link}")
            else:
                link = create_calendar_task(text, parsed_time)
                send_message(chat_id, f"✅ Tarea añadida al calendario: {link}")
        else:
            send_message(chat_id, "⚠️ No encontré una fecha u hora en tu mensaje.")

    # --- Mensaje de voz ---
    elif "voice" in message:
        voice = message["voice"]
        file_id = voice["file_id"]
        duration = voice.get("duration", 0)

        send_message(chat_id, "🎧 Procesando tu nota de voz...")

        try:
            audio_data = download_file(file_id)
            text = transcribe_audio(audio_data)

            if not text:
                send_message(chat_id, "⚠️ No pude transcribir el audio.")
                return {"ok": True}

            send_message(chat_id, f"🗣️ He entendido: {text}")

            event_type = detect_event_type(text)
            parsed_time = parse_datetime_from_text(text)

            if parsed_time:
                if event_type == "evento":
                    link = create_calendar_event(text, pars
