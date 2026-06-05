"""
Run this ONCE locally to generate your Gmail OAuth refresh token.

Prerequisites:
    pip install google-auth-oauthlib

Usage:
    python get_token.py

A browser window will open for Google sign-in. After authorising, copy the
three values printed below into your .env file.
"""

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.compose'
]

flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
creds = flow.run_local_server(port=0)

print()
print('Add these to your .env:')
print(f'GMAIL_CLIENT_ID={creds.client_id}')
print(f'GMAIL_CLIENT_SECRET={creds.client_secret}')
print(f'GMAIL_REFRESH_TOKEN={creds.refresh_token}')

