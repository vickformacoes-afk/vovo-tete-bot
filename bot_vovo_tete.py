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
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "vovo-tete-123") or "vovo-tete-123"
MODEL_KEY = os.getenv("MODEL_API_KEY")
MODEL_BASE = os.getenv("MODEL_BASE_URL") or "https://api.meta.ai/v1"
MODEL_NAME = (os.getenv("MODEL_NAME") or "muse-spark-1.3").strip()
FALLBACK_MODELS = ["muse-spark-1.3", "muse-spark-1.3-contributor", "muse-spark-1.2"]

app = Flask(__name__)

SYSTEM_PROMPT = """Es a Vovo Tete, dona das Delicias da Vovo Tete.
Falas com carinho, como uma avo: 'meu amor, minha filha'.
Vendes bolos, doces e salgados caseiros por encomenda.
Responde em portugues simples e curto (max 3 frases).
Objetivo: perceber o que quer, para quando, e mandar para o WhatsApp.
Nunca inventes precos. Se nao souberes, diz: 'Deixa a vo confirmar e ja te respondo, ta?'
"""

def muse_reply(user_text: str) -> str:
    fallback = "Ola, meu amor! Obrigada pela mensagem. Ja ja a Vovo responde. Qual bolo queres encomendar?"
    if not MODEL_KEY or MODEL_KEY.startswith("SEU_") or "COLOCA" in MODEL_KEY:
        return fallback
    client = OpenAI(api_key=MODEL_KEY, base_url=MODEL_BASE)
    tried = [MODEL_NAME] + [m for m in FALLBACK_MODELS if m != MODEL_NAME]
    for m in tried:
        try:
            r = client.chat.completions.create(
                model=m,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_text},
                ],
                max_tokens=2000,
                temperature=0.7,
            )
            txt = r.choices[0].message.content
            if txt:
                print(f"muse_ok model={m}")
                return txt.strip()
        except Exception as e:
            print(f"muse_fail model={m}: {str(e)[:300]}")
            continue
    return fallback

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
    try:
        data = request.json
        print(data)
        for entry in data.get("entry", []):
            for ev in entry.get("messaging", []):
                psid = ev["sender"]["id"]
                if ev.get("message", {}).get("text"):
                    texto = ev["message"]["text"]
                    try:
                        resposta = muse_reply(texto)
                    except Exception as e:
                        print("reply_error:", e)
                        resposta = "Ola, meu amor! Obrigada pela mensagem. Ja ja a Vovo responde."
                    try:
                        send_messenger(psid, resposta)
                    except Exception as e:
                        print("send_error:", e)
    except Exception as e:
        print("webhook_error:", e)
    return jsonify({"ok": True})

import datetime
CRON_SECRET = os.getenv("CRON_SECRET", "vovo-cron-123")

TEMAS_SEMANA = {
    0: "segunda de recomeço: bolo fofinho para começar bem a semana",
    1: "terça de carinho: doce que abraça",
    2: "quarta de meio da semana: merece um mimo",
    3: "quinta de antecipar o fim de semana: encomenda já o bolo de sábado",
    4: "sexta de festa: bolo para o fim de semana em família",
    5: "sábado de festa e encomendas abertas",
    6: "domingo em família com sobremesa da Vovó",
}

def gerar_legenda_diaria() -> str:
    hoje = datetime.date.today()
    tema = TEMAS_SEMANA[hoje.weekday()]
    prompt = (
        f"Escreve legenda curta para Facebook da pagina Delicias da Vovo Tete. "
        f"Tema de hoje ({hoje.strftime('%d/%m')}, {tema}). "
        f"Tom de avo carinhosa, 2-4 frases, 1 pergunta no fim para gerar comentarios, "
        f"3 hashtags. Menciona encomendas abertas no WhatsApp. Max 600 caracteres."
    )
    if not MODEL_KEY or "COLOCA" in (MODEL_KEY or ""):
        return (
            f"🥰 Bom dia, meus amores! Hoje é {tema}. "
            f"Encomendas abertas na Vovó Teté! Qual bolo queres para hoje? Conta aqui 👇 "
            f"#DeliciasDaVovoTete #FeitoComAmor #BolosCaseiros"
        )
    client = OpenAI(api_key=MODEL_KEY, base_url=MODEL_BASE)
    for m in [MODEL_NAME] + [x for x in FALLBACK_MODELS if x != MODEL_NAME]:
        try:
            r = client.chat.completions.create(
                model=m,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000, temperature=0.8,
            )
            txt = r.choices[0].message.content
            if txt:
                return txt.strip()[:1500]
        except Exception as e:
            print(f"legenda_fail {m}: {str(e)[:200]}")
            continue
    return "🥰 Bom dia da Vovó Teté! Encomendas abertas hoje. Qual doce te faz feliz? 👇 #DeliciasDaVovoTete"

FOTOS_SEMANA = {
    0: ("https://images.unsplash.com/photo-1578985545062-69928b1d9587?q=80&w=1080&auto=format&fit=crop", "COMEÇA BEM A SEMANA", "Bolo de Chocolate!"),
    1: ("https://images.unsplash.com/photo-1565958011703-44f9829ba187?q=80&w=1080&auto=format&fit=crop", "HOJE NA VOVÓ TETÉ", "Bolo de Morango!"),
    2: ("https://images.unsplash.com/photo-1509440159596-0249088772ff?q=80&w=1080&auto=format&fit=crop", "SAÍDO DO FORNO", "Pão Caseiro!"),
    3: ("https://images.unsplash.com/photo-1551024506-0bccd828d307?q=80&w=1080&auto=format&fit=crop", "DOCE TENTAÇÃO", "Sobremesa Cremosa!"),
    4: ("https://images.unsplash.com/photo-1578985545062-69928b1d9587?q=80&w=1080&auto=format&fit=crop", "SEXTA DE FESTA", "Bolo p/ o Fim de Semana!"),
    5: ("https://images.unsplash.com/photo-1565958011703-44f9829ba187?q=80&w=1080&auto=format&fit=crop", "SÁBADO NA VOVÓ", "Encomenda Aberta!"),
    6: ("https://images.unsplash.com/photo-1509440159596-0249088772ff?q=80&w=1080&auto=format&fit=crop", "DOMINGO EM FAMÍLIA", "Mesa Cheia de Amor!"),
}

def gerar_arte_diaria(dest_path: str) -> str:
    from PIL import Image, ImageDraw, ImageFont
    import urllib.request
    hoje = datetime.date.today()
    url, top1, top2 = FOTOS_SEMANA[hoje.weekday()]
    tmp = dest_path + ".src.jpg"
    urllib.request.urlretrieve(url, tmp)
    img = Image.open(tmp).convert("RGB")
    w, h = img.size
    s = max(1080 / w, 1080 / h)
    img = img.resize((int(w * s), int(h * s)), Image.LANCZOS)
    x = (img.width - 1080) // 2; y = (img.height - 1080) // 2
    img = img.crop((x, y, x + 1080, y + 1080))
    ov = Image.new("RGBA", (1080, 1080), (0, 0, 0, 0))
    do = ImageDraw.Draw(ov)
    for yy in range(1080):
        if yy < 340:
            do.line([(0, yy), (1080, yy)], fill=(60, 30, 5, int(165 * (1 - yy / 340))))
        if yy > 600:
            do.line([(0, yy), (1080, yy)], fill=(40, 20, 5, int(215 * ((yy - 600) / 480))))
    bg = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
    d = ImageDraw.Draw(bg)

    def lf(name, size):
        p = f"C:\\Windows\\Fonts\\{name}"
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
        for alt in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"]:
            if os.path.exists(alt):
                try:
                    return ImageFont.truetype(alt, size)
                except Exception:
                    pass
        return ImageFont.load_default()
    f_s = lf("arialbd.ttf", 36); f_t = lf("georgiai.ttf", 80)
    f_b = lf("georgiab.ttf", 62); f_u = lf("arial.ttf", 38); f_c = lf("arialbd.ttf", 40)

    def ct(yy, t, f, fill, st=2):
        bb = d.textbbox((0, 0), t, font=f, stroke_width=st)
        d.text(((1080 - (bb[2] - bb[0])) / 2, yy), t, font=f, fill=fill, stroke_width=st, stroke_fill="#3a1e05")
    ct(55, top1, f_s, "#FFD9A8", 1)
    ct(125, top2, f_t, "white")
    d.rounded_rectangle([110, 690, 970, 850], radius=30, fill="#FFF8F0")
    bb = d.textbbox((0, 0), "Delícias da Vovó Teté", font=f_b)
    d.text(((1080 - (bb[2] - bb[0])) / 2, 722), "Delícias da Vovó Teté", font=f_b, fill="#5D2E0C")
    ct(860, "Bolos • Doces • Salgados caseiros", f_u, "white")
    d.rounded_rectangle([240, 920, 840, 1010], radius=45, fill="#C0392B")
    ct(938, "Encomenda no WhatsApp", f_c, "white", 0)
    bg.save(dest_path, "PNG")
    try:
        os.remove(tmp)
    except Exception:
        pass
    return dest_path

@app.get("/cron/daily")
def cron_daily():
    if request.args.get("secret") != CRON_SECRET:
        return jsonify({"ok": False, "error": "bad secret"}), 403
    dry = request.args.get("dry", "0") == "1"
    legenda = gerar_legenda_diaria()
    feito = os.path.join(os.path.dirname(__file__), "post-estreia-vovo-tete.png")
    arte = os.path.join("/tmp", "arte-vovo-hoje.png") if os.path.isdir("/tmp") else os.path.join(os.path.dirname(__file__), "arte-vovo-hoje.png")
    try:
        gerar_arte_diaria(arte)
        img = arte
    except Exception as e:
        print("arte_fail, usa estatica:", e)
        img = feito
    if dry or not os.path.exists(img):
        return jsonify({"ok": True, "dry": dry, "legenda": legenda, "image": img, "image_found": os.path.exists(img)})
    try:
        res = publish_post(legenda, img)
        return jsonify({"ok": True, "legenda": legenda, "arte": os.path.basename(img), "fb": res})
    except Exception as e:
        print("cron_post_error:", e)
        return jsonify({"ok": False, "error": str(e)[:500], "legenda": legenda}), 500

@app.get("/")
def home():
    return "Bot Vovo Tete online. GET /webhook para verificar."

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
