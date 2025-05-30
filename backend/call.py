import os
import json
import base64
import asyncio
import websockets
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
import google.generativeai as genai
from dotenv import load_dotenv
from pymongo import MongoClient
from twilio.rest import Client  # Twilio REST client for outbound calls

load_dotenv()

# Configure Gemini API
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
gemini_model = genai.GenerativeModel('gemini-1.5-flash')

# MongoDB setup
client = MongoClient(os.getenv("DATABASE_URL"))
db = client["user_data"]
collection = db["data"]

# Twilio setup
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")

# Twilio REST client for making outbound calls
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Configuration
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
PORT = int(os.getenv('PORT', 5050))

if not DEEPGRAM_API_KEY:
    raise ValueError('Missing the Deepgram API key. Please set it in the .env file.')

app = FastAPI()
conv_history = []

def initiate_outbound_call(to_number):
    try:
        call = twilio_client.calls.create(
            to=to_number,
            from_=TWILIO_PHONE_NUMBER,
            url="http://demo.twilio.com/docs/voice.xml"  # Simple Twilio demo voice XML
        )
        print(f"Outbound call initiated! SID: {call.sid}")
        return call.sid
    except Exception as e:
        print(f"Error initiating outbound call: {e}")
        return None

@app.get("/")
async def index_page():
    return {"message": "Twilio Media Stream Server is running!"}

@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    response = VoiceResponse()
    response.say("Please wait while we connect you to the recruiter.")
    response.pause(length=1)
    response.say("The interview is starting now.")
    host = request.url.hostname
    connect = Connect()
    connect.stream(url=f'wss://{host}/media-stream')
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    print("Client connected")
    await websocket.accept()

    stream_state = {
        'stream_sid': None,
        'latest_audio_timestamp': 0,
        'last_assistant_item': None,
        'response_start_timestamp_twilio': None,
        'mark_queue': []
    }

    async def receive_from_twilio():
        try:
            async for message in websocket.iter_text():
                data = json.loads(message)
                if data['event'] == 'media':
                    payload = base64.b64decode(data['media']['payload'])
                    await deepgram_ws.send(payload)
                elif data['event'] == 'start':
                    stream_state['stream_sid'] = data['start']['streamSid']
                    print(f"Stream started: {stream_state['stream_sid']}")
                elif data['event'] == 'stop':
                    print("Stream ended.")
                    with open('output.txt', 'w') as file:
                        json.dump(conv_history, file)
                    extract_and_update()
        except Exception as e:
            print(f"Error receiving from Twilio: {e}")

    async def send_to_twilio():
        try:
            async for msg in deepgram_ws:
                transcript = json.loads(msg)
                if transcript.get("type") == "Results":
                    words = transcript["channel"]["alternatives"][0]["words"]
                    user_text = " ".join(word["word"] for word in words)
                    print(f"Transcribed: {user_text}")
                    conv_history.append({"user": user_text})

                    ai_prompt = f"""
                    Act like an HR recruiter conducting a phone interview. 
                    Job Role: Extracted from candidate.json
                    Resume: Extracted from candidate.json
                    Ask relevant questions, listen to answers, and acknowledge.
                    User said: {user_text}
                    Respond appropriately.
                    """

                    ai_response = gemini_model.generate_content(ai_prompt).text.strip()
                    print(f"AI Response: {ai_response}")
                    conv_history.append({"assistant": ai_response})

                    audio_response = {
                        "event": "media",
                        "streamSid": stream_state['stream_sid'],
                        "media": {
                            "payload": base64.b64encode(ai_response.encode()).decode()
                        }
                    }
                    await websocket.send_json(audio_response)

        except Exception as e:
            print(f"Error receiving from Deepgram: {e}")

    async def initialize_session():
        system_prompt = """
        You are Carla, a recruiter at Coca-Cola. Start the phone screening by introducing yourself, explaining the process, and asking the candidate's name.
        """
        ai_response = gemini_model.generate_content(system_prompt).text.strip()
        print(f"System Prompt Response: {ai_response}")
        conv_history.append({"assistant": ai_response})

        audio_init = {
            "event": "media",
            "streamSid": stream_state['stream_sid'],
            "media": {
                "payload": base64.b64encode(ai_response.encode()).decode()
            }
        }
        await websocket.send_json(audio_init)

    async with websockets.connect(
        "wss://api.deepgram.com/v1/listen",
        extra_headers={
            "Authorization": f"Token {DEEPGRAM_API_KEY}"
        },
        ping_interval=5,
        ping_timeout=30
    ) as deepgram_ws:
        await initialize_session()
        await asyncio.gather(receive_from_twilio(), send_to_twilio())

def extract_and_update():
    try:
        with open('candidate.json', 'r') as f:
            candidate = json.load(f)

        uid = candidate['UID']
        phone_number = candidate.get('Phone')

        with open('output.txt', 'r') as file:
            log = file.read()

        prompt = f"""
        Grade this candidate on a scale of 1–10 based on the phone screening transcript below:
        Transcript:
        {log}
        Return the final score and detailed comments.
        """

        response = gemini_model.generate_content(prompt)
        content = response.text.strip()

        final_score = 8  # Fallback score
        comments = content

        collection.update_one({"UID": uid}, {"$set": {"secondary_score": final_score, "phone_screen_notes": comments, "phone_screen": "completed"}})
        print("Database updated successfully")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    import uvicorn
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "call":
        test_number = "+916399493891"  # Replace with the candidate's phone number
        initiate_outbound_call(test_number)
    else:
        uvicorn.run(app, host="0.0.0.0", port=PORT)
