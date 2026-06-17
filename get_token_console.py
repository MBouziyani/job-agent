"""Run OAuth device flow on headless VPS — no browser needed.

You paste a URL into your phone/computer browser, authorize, and tokens print here.
"""
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import os, json

SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.compose',
]

flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
creds = flow.run_console()

print()
print('✅  Authorization successful!')
print()
print('Add these to your .env file:')
print(f'GMAIL_CLIENT_ID={creds.client_id}')
print(f'GMAIL_CLIENT_SECRET={creds.client_secret}')
print(f'GMAIL_REFRESH_TOKEN={creds.refresh_token}')
print()
print(f'Token expiry: {creds.expiry}')
