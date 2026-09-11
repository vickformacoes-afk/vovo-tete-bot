"""
Bot ponte: Facebook Page (Messenger + Posts) <-> Muse Spark
Delicias da Vovo Tete

Instalar: pip install -r requirements.txt
Correr: python bot_vovo_tete.py
"""
import os
from flask import Flask, request, jsonify
import requests
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

PAGE_ID = os.getenv("PAGE_ID")
PAGE_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "vovo-tete-123")
MODEL_KEY = os.getenv("MODEL_API_KEY")
MODEL_BASE = os.getenv("MODEL_BASE_URL", "https://api.meta.ai/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "muse-spark-1.3")

app = Flask(__name__)

SYSTEM_PROMPT = """Es a Vovo Tete, dona das Delicias da Vovo Tete.
Falas com carinho, como uma avo: 'meu amor, minha filha'.
Vendes bolos, doces e salgados caseiros por encomenda.
Responde em portugues simples e curto (max 3 frases).
Objetivo: perceber o que quer, para quando, e mandar para o WhatsApp.
Nunca inventes precos. Se nao souberes, diz: 'Deixa a vo confirmar e ja te respondo, ta?'
"""

def muse_reply(user_text: str) -> str:
    if not MODEL_KEY or MODEL_KEY.startswith("SEU_"):
        return "Ola, meu amor! Obrigada pela mensagem. Ja ja a Vovo responde. Qual bolo queres encomendar?"
    client = OpenAI(api_key=MODEL_KEY, base_url=MODEL_BASE)
    r = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        max_tokens=2000,
        temperature=0.7,
    )
    txt = r.choices[0].message.content
    if not txt:
        return "Ola, meu amor! Obrigada pela mensagem. Ja ja a Vovo responde. Qual bolo queres encomendar?"
    return txt.strip()

def send_messenger(psid: str, text: str):
    url = "https://graph.facebook.com/v21.0/me/messages"
    resp = requests.post(url, params={"access_token": PAGE_TOKEN}, json={
        "recipient": {"id": psid},
        "message": {"text": text[:1900]},
    }, timeout=20)
    print("send:", resp.status_code, resp.text)
    return resp.ok

def publish_post(message: str, image_path: str | None = None):
    if image_path:
        url = f"https://graph.facebook.com/v21.0/{PAGE_ID}/photos"
        with open(image_path, "rb") as f:
            r = requests.post(url, params={"access_token": PAGE_TOKEN},
                              data={"caption": message}, files={"source": f}, timeout=60)
    else:
        url = f"https://graph.facebook.com/v21.0/{PAGE_ID}/feed"
        r = requests.post(url, params={"access_token": PAGE_TOKEN},
                          json={"message": message}, timeout=20)
    print("post:", r.status_code, r.text)
    return r.json()

@app.get("/webhook")
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "erro verify", 403

@app.post("/webhook")
def incoming():
    data = request.json
    print(data)
    for entry in data.get("entry", []):
        for ev in entry.get("messaging", []):
            psid = ev["sender"]["id"]
            if ev.get("message", {}).get("text"):
                texto = ev["message"]["text"]
                resposta = muse_reply(texto)
                send_messenger(psid, resposta)
    return jsonify({"ok": True})

@app.get("/")
def home():
    return "Bot Vovo Tete online. GET /webhook para verificar."

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
