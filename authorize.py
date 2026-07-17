#!/usr/bin/env python3
"""ตัวช่วยล็อกอิน Google ครั้งแรก — พ่น URL ให้ก๊อปไปเปิดเอง แล้วเก็บ token.json"""
import os
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

flow = InstalledAppFlow.from_client_secrets_file(
    os.path.join(BASE_DIR, "client_secret.json"), SCOPES
)
creds = flow.run_local_server(
    port=8765,
    open_browser=False,
    authorization_prompt_message="เปิด URL นี้ในเบราว์เซอร์:\n{url}",
    success_message="ล็อกอินสำเร็จ! ปิดแท็บนี้ได้เลย",
)
with open(os.path.join(BASE_DIR, "token.json"), "w", encoding="utf-8") as f:
    f.write(creds.to_json())
print("\nเก็บ token.json เรียบร้อย ✅")
