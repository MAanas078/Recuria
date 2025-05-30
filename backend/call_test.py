# this for twillo testing 

# call_test.py

import os
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()

# Twilio credentials
account_sid = os.getenv("TWILIO_ACCOUNT_SID")
auth_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_number = os.getenv("TWILIO_PHONE_NUMBER")

client = Client(account_sid, auth_token)

# Candidate ka number (replace with actual candidate number from candidate.json)
to_number = "+916399493891"  # Replace with the actual number

# Call initiate karna
call = client.calls.create(
    to=to_number,
    from_=twilio_number,
    url="http://demo.twilio.com/docs/voice.xml"  # Basic Twilio demo voice
)

print(f"Call initiated! SID: {call.sid}")
