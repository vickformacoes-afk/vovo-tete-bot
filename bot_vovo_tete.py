"""
Bot ponte: Facebook Page (Messenger + Posts) <-> Muse Spark
Delicias da Vovo Tete — com Dashboard de Aprovacao

Instalar: pip install -r requirements.txt
Correr: python bot_vovo_tete.py
"""
import os, datetime, json, sqlite3, uuid
from flask import Flask, request, jsonify, send_from_directory, render_template_string
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
CRON_SECRET = os.getenv("CRON_SECRET", "vovo-cron-123")
DASHBOARD_SECRET = os.getenv("DASHBOARD_SECRET", "vovo-dash-123")

app = Flask(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "vovo.db")
ARTES_DIR = os.path.join(os.path.dirname(__file__), "artes")
os.makedirs(ARTES_DIR, exist_ok=True)

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS posts (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            legenda TEXT,
            arte_path TEXT,
            tema TEXT,
            scheduled_for TEXT,
            published_at TEXT,
            fb_post_id TEXT,
            fb_response TEXT,
            rejected_reason TEXT,
            recreate_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at TEXT NOT NULL,
            psid TEXT,
            sender_name TEXT,
            text TEXT,
            reply TEXT
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    conn.commit()
    conn.close()

init_db()

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
                texto = ev.get("message", {}).get("text", "")
                if texto:
                    resposta = muse_reply(texto)
                    conn = get_db()
                    conn.execute(
                        "INSERT INTO messages (received_at, psid, text, reply) VALUES (?, ?, ?, ?)",
                        (datetime.datetime.now().isoformat(), psid, texto, resposta)
                    )
                    conn.commit()
                    conn.close()
                    try:
                        send_messenger(psid, resposta)
                    except Exception as e:
                        print("send_error:", e)
    except Exception as e:
        print("webhook_error:", e)
    return jsonify({"ok": True})

TEMAS_SEMANA = {
    0: "segunda de recomeco: bolo fofinho para comecar bem a semana",
    1: "terca de carinho: doce que abraca",
    2: "quarta de meio da semana: merece um mimo",
    3: "quinta de antecipar o fim de semana: encomenda ja o bolo de sabado",
    4: "sexta de festa: bolo para o fim de semana em familia",
    5: "sabado de festa e encomendas abertas",
    6: "domingo em familia com sobremesa da Vovo",
}

FOTOS_SEMANA = {
    0: ("https://images.unsplash.com/photo-1578985545062-69928b1d9587?q=80&w=1080&auto=format&fit=crop", "COMECABEM A SEMANA", "Bolo de Chocolate!"),
    1: ("https://images.unsplash.com/photo-1565958011703-44f9829ba187?q=80&w=1080&auto=format&fit=crop", "HOJE NA VOVO TETE", "Bolo de Morango!"),
    2: ("https://images.unsplash.com/photo-1509440159596-0249088772ff?q=80&w=1080&auto=format&fit=crop", "SAIDO DO FORNO", "Pao Caseiro!"),
    3: ("https://images.unsplash.com/photo-1551024506-0bccd828d307?q=80&w=1080&auto=format&fit=crop", "DOCE TENTACAO", "Sobremesa Cremosa!"),
    4: ("https://images.unsplash.com/photo-1578985545062-69928b1d9587?q=80&w=1080&auto=format&fit=crop", "SEXTA DE FESTA", "Bolo p/ o Fim de Semana!"),
    5: ("https://images.unsplash.com/photo-1565958011703-44f9829ba187?q=80&w=1080&auto=format&fit=crop", "SABADO NA VOVO", "Encomenda Aberta!"),
    6: ("https://images.unsplash.com/photo-1509440159596-0249088772ff?q=80&w=1080&auto=format&fit=crop", "DOMINGO EM FAMILIA", "Mesa Cheia de Amor!"),
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
            f"Bom dia, meus amores! Hoje e {tema}. "
            f"Encomendas abertas na Vovo Tetel Qual bolo queres para hoje? Conta aqui "
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
    return "Bom dia da Vovo Tetel Encomendas abertas hoje. Qual doce te faz feliz? #DeliciasDaVovoTete"

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
    bb = d.textbbox((0, 0), "Delicias da Vovo Teté", font=f_b)
    d.text(((1080 - (bb[2] - bb[0])) / 2, 722), "Delicias da Vovo Teté", font=f_b, fill="#5D2E0C")
    ct(860, "Bolos - Doces - Salgados caseiros", f_u, "white")
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
    hoje = datetime.date.today()
    tema = TEMAS_SEMANA[hoje.weekday()]
    legenda = gerar_legenda_diaria()
    post_id = str(uuid.uuid4())[:8]
    arte_filename = f"arte-{post_id}.png"
    arte_path = os.path.join(ARTES_DIR, arte_filename)
    arte_ok = False
    try:
        gerar_arte_diaria(arte_path)
        arte_ok = True
    except Exception as e:
        print("arte_fail:", e)
        arte_path = ""
    conn = get_db()
    conn.execute(
        "INSERT INTO posts (id, created_at, status, legenda, arte_path, tema, scheduled_for) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (post_id, datetime.datetime.now().isoformat(), "pending" if not dry else "draft",
         legenda, arte_path if arte_ok else "", tema, hoje.isoformat())
    )
    conn.commit()
    conn.close()
    if dry:
        return jsonify({"ok": True, "dry": True, "post_id": post_id, "legenda": legenda,
                        "arte": arte_path if arte_ok else None, "tema": tema})
    return jsonify({"ok": True, "post_id": post_id, "status": "pending", "message": "Post guardado para aprovacao. Abre /dashboard para rever."})

@app.post("/admin/update-token")
def update_token():
    secret = request.args.get("secret") or (request.json or {}).get("secret")
    if secret != CRON_SECRET:
        return jsonify({"ok": False, "error": "bad secret"}), 403
    new_token = (request.json or {}).get("token", "")
    if not new_token or len(new_token) < 50:
        return jsonify({"ok": False, "error": "token invalido"}), 400
    global PAGE_TOKEN
    PAGE_TOKEN = new_token
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    try:
        lines = []
        found = False
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("PAGE_ACCESS_TOKEN="):
                        lines.append(f"PAGE_ACCESS_TOKEN={new_token}\n")
                        found = True
                    else:
                        lines.append(line)
        if not found:
            lines.append(f"PAGE_ACCESS_TOKEN={new_token}\n")
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except Exception as e:
        print("env_write_error:", e)
    return jsonify({"ok": True, "message": "Token atualizado com sucesso!"})

@app.get("/admin/check-token")
def check_token():
    secret = request.args.get("secret")
    if secret != CRON_SECRET:
        return jsonify({"ok": False, "error": "bad secret"}), 403
    try:
        r = requests.get("https://graph.facebook.com/v21.0/me", params={"access_token": PAGE_TOKEN}, timeout=10)
        data = r.json()
        ok = "id" in data
        return jsonify({"ok": ok, "page": data.get("name", ""), "page_id": data.get("id", ""), "token_start": PAGE_TOKEN[:30] + "..."})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.get("/artes/<path:filename>")
def serve_arte(filename):
    return send_from_directory(ARTES_DIR, filename)

def check_dashboard_auth():
    token = request.args.get("token") or request.headers.get("X-Dashboard-Token")
    return token == DASHBOARD_SECRET

@app.get("/dashboard")
def dashboard():
    if not check_dashboard_auth():
        return "Acesso negado. Adiciona ?token=vovo-dash-123", 403
    return render_template_string(DASHBOARD_HTML)

@app.get("/api/posts")
def api_posts():
    if not check_dashboard_auth():
        return jsonify({"error": "unauthorized"}), 401
    status = request.args.get("status", "")
    conn = get_db()
    if status:
        rows = conn.execute("SELECT * FROM posts WHERE status=? ORDER BY created_at DESC", (status,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM posts ORDER BY created_at DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.post("/api/posts/<post_id>/approve")
def api_approve(post_id):
    if not check_dashboard_auth():
        return jsonify({"error": "unauthorized"}), 401
    conn = get_db()
    row = conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "post not found"}), 404
    if row["status"] != "pending":
        conn.close()
        return jsonify({"error": f"post is {row['status']}, not pending"}), 400
    arte = row["arte_path"] if row["arte_path"] and os.path.exists(row["arte_path"]) else None
    try:
        fb = publish_post(row["legenda"], arte)
        conn.execute("UPDATE posts SET status='published', published_at=?, fb_response=? WHERE id=?",
                     (datetime.datetime.now().isoformat(), json.dumps(fb), post_id))
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "fb": fb})
    except Exception as e:
        conn.close()
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/api/posts/<post_id>/reject")
def api_reject(post_id):
    if not check_dashboard_auth():
        return jsonify({"error": "unauthorized"}), 401
    data = request.json or {}
    reason = data.get("reason", "")
    conn = get_db()
    conn.execute("UPDATE posts SET status='rejected', rejected_reason=? WHERE id=?", (reason, post_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

@app.post("/api/posts/<post_id>/recreate")
def api_recreate(post_id):
    if not check_dashboard_auth():
        return jsonify({"error": "unauthorized"}), 401
    conn = get_db()
    row = conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "post not found"}), 404
    new_legenda = gerar_legenda_diaria()
    new_id = str(uuid.uuid4())[:8]
    arte_filename = f"arte-{new_id}.png"
    arte_path = os.path.join(ARTES_DIR, arte_filename)
    arte_ok = False
    try:
        gerar_arte_diaria(arte_path)
        arte_ok = True
    except Exception as e:
        print("recreate_arte_fail:", e)
        arte_path = ""
    conn.execute(
        "INSERT INTO posts (id, created_at, status, legenda, arte_path, tema, scheduled_for, recreate_count) VALUES (?, ?, 'pending', ?, ?, ?, ?, ?)",
        (new_id, datetime.datetime.now().isoformat(), new_legenda, arte_path if arte_ok else "",
         row["tema"], row["scheduled_for"], (row["recreate_count"] or 0) + 1)
    )
    conn.execute("UPDATE posts SET status='recreated' WHERE id=?", (post_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "new_post_id": new_id})

@app.get("/api/stats")
def api_stats():
    if not check_dashboard_auth():
        return jsonify({"error": "unauthorized"}), 401
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) c FROM posts").fetchone()["c"]
    pending = conn.execute("SELECT COUNT(*) c FROM posts WHERE status='pending'").fetchone()["c"]
    published = conn.execute("SELECT COUNT(*) c FROM posts WHERE status='published'").fetchone()["c"]
    rejected = conn.execute("SELECT COUNT(*) c FROM posts WHERE status='rejected'").fetchone()["c"]
    msg_count = conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
    conn.close()
    return jsonify({"total_posts": total, "pending": pending, "published": published,
                    "rejected": rejected, "total_messages": msg_count})

@app.get("/api/messages")
def api_messages():
    if not check_dashboard_auth():
        return jsonify({"error": "unauthorized"}), 401
    limit = request.args.get("limit", 50, type=int)
    conn = get_db()
    rows = conn.execute("SELECT * FROM messages ORDER BY received_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.get("/")
def home():
    return "Bot Vovo Tete online. GET /webhook para verificar. Dashboard: /dashboard?token=vovo-dash-123"

@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(os.path.join(os.path.dirname(__file__), "static"), filename)

@app.get("/politica-privacidade")
def politica_privacidade():
    return render_template_string(PRIVACIDADE_HTML)

PRIVACIDADE_HTML = r"""
<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/x-icon" href="/static/favicon.ico">
<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
<link rel="icon" type="image/png" href="/static/favicon.png">
<title>Politica de Privacidade — Delicias da Vovo Teté</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#fdf6ee;color:#3a1e05;line-height:1.7}
.hero{background:linear-gradient(135deg,#8B4513,#C0392B);color:white;padding:50px 30px;text-align:center}
.hero h1{font-size:2rem;margin-bottom:10px}
.hero p{font-size:1rem;opacity:.9}
.container{max-width:800px;margin:0 auto;padding:30px 20px}
section{background:white;border-radius:16px;padding:30px;margin-bottom:20px;box-shadow:0 2px 10px rgba(0,0,0,.06)}
h2{color:#8B4513;font-size:1.3rem;margin-bottom:12px;border-bottom:2px solid #f0e0cc;padding-bottom:8px}
p,li{margin-bottom:10px;font-size:.95rem}
ul{padding-left:20px}
li{margin-bottom:6px}
.highlight{background:#fdf6ee;padding:16px;border-radius:10px;border-left:4px solid #C0392B;margin:15px 0}
.footer{text-align:center;padding:30px;color:#a08060;font-size:.85rem}
a{color:#C0392B;text-decoration:none;font-weight:600}
a:hover{text-decoration:underline}
</style>
</head>
<body>
<div class="hero">
  <img src="/static/icon-privacidade.png" alt="Privacidade" style="width:80px;height:80px;margin-bottom:15px">
  <h1>Politica de Privacidade</h1>
  <p>Delicias da Vovo Teté — Bolos, Doces e Salgados Caseiros</p>
</div>
<div class="container">

<section>
  <h2>1. Informacoes que Recolhemos</h2>
  <p>Ao interagir com a nossa pagina no Facebook e usar os nossos servicos, podemos recolher as seguintes informacoes:</p>
  <ul>
    <li><strong>Nome e foto de perfil</strong> do Facebook</li>
    <li><strong>Mensagens</strong> enviadas atraves do Messenger</li>
    <li><strong>Dados de encomendas</strong> como nome, telefone e preferencias</li>
    <li><strong>Informacoes de uso</strong> como horario de interacao e tipo de interacao</li>
  </ul>
</section>

<section>
  <h2>2. Como Usamos as suas Informacoes</h2>
  <p>Utilizamos os dados recolhidos para:</p>
  <ul>
    <li>Responder às suas mensagens e encomendas</li>
    <li>Melhorar os nossos servicos e atendimento</li>
    <li>Enviar informacoes sobre promocoes e novidades (com o seu consentimento)</li>
    <li>Garantir a seguranca da nossa plataforma</li>
  </ul>
</section>

<section>
  <h2>3. Compartilhamento de Dados</h2>
  <p>Nao vendemos nem compartilhamos os seus dados pessoais com terceiros, exceto quando:</p>
  <ul>
    <li>Obrigatorio por lei</li>
    <li>Necessario para prestar o servico solicitado (ex: processamento de pagamentos)</li>
    <li>Com o seu consentimento explicito</li>
  </ul>
</section>

<section>
  <h2>4. Seguranca dos Dados</h2>
  <div class="highlight">
    <p>Adotamos medidas de seguranca adequadas para proteger os seus dados contra acesso nao autorizado, alteracao, divulgacao ou destruicao.</p>
  </div>
</section>

<section>
  <h2>5. Os seus Direitos</h2>
  <p>Tem direito a:</p>
  <ul>
    <li><strong>Acessar</strong> os seus dados pessoais</li>
    <li><strong>Corrigir</strong> dados incorretos</li>
    <li><strong>Solicitar a eliminacao</strong> dos seus dados</li>
    <li><strong>Oponhar-se</strong> ao processamento dos seus dados</li>
    <li><strong>Revogar o consentimento</strong> a qualquer momento</li>
  </ul>
</section>

<section>
  <h2>6. Cookies e Tecnologias Semelhantes</h2>
  <p>A nossa plataforma pode usar cookies e tecnologias semelhantes para melhorar a experiencia do utilizador e recolher informacoes de uso.</p>
</section>

<section>
  <h2>7. Menores de Idade</h2>
  <p>Os nossos servicos nao sao direcionados a menores de 13 anos. Nao recolhemos intencionalmente dados de menores.</p>
</section>

<section>
  <h2>8. Alteracoes a Politica</h2>
  <p>Podemos atualizar esta politica periodicamente. Notificaremos sobre alteracoes significativas atraves da nossa pagina no Facebook.</p>
</section>

<section>
  <h2>9. Contacto</h2>
  <p>Para questoes sobre esta politica de privacidade ou para exercer os seus direitos:</p>
  <div class="highlight">
    <p><strong>Pagina:</strong> Delicias da Vovo Teté<br>
    <strong>Facebook:</strong> facebook.com/DeliciasDaVovoTete<br>
    <strong>WhatsApp:</strong> +244 923 303 389<br>
    <strong>Email:</strong> vovotete@delicias.com</p>
  </div>
</section>

<section>
  <h2>10. Legislacao Aplicavel</h2>
  <p>Esta politica é regida pela legislacao da Republica de Angola e pelo Regulamento Geral sobre a Proteccao de Dados (RGPD).</p>
</section>

</div>
<div class="footer">
  <p>Ultima atualizacao: Janeiro 2025</p>
  <p style="margin-top:10px"><a href="/dashboard?token=vovo-dash-123">Voltar ao Painel</a></p>
</div>
</body>
</html>
"""

DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/x-icon" href="/static/favicon.ico">
<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
<link rel="icon" type="image/png" href="/static/favicon.png">
<title>Vovo Tete - Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#fdf6ee;color:#3a1e05}
.header{background:linear-gradient(135deg,#8B4513,#C0392B);color:white;padding:20px 30px;display:flex;align-items:center;justify-content:space-between;box-shadow:0 2px 10px rgba(0,0,0,.2)}
.header h1{font-size:1.6rem}
.header .stats{display:flex;gap:20px;font-size:.9rem}
.header .stats span{background:rgba(255,255,255,.2);padding:6px 14px;border-radius:20px}
.container{max-width:1200px;margin:20px auto;padding:0 20px}
.tabs{display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap}
.tab{padding:10px 20px;border:none;border-radius:25px;cursor:pointer;font-size:.95rem;font-weight:600;background:#f0e0cc;color:#5D2E0C;transition:.2s}
.tab:hover{background:#e0c8a8}
.tab.active{background:#C0392B;color:white}
.post-card{background:white;border-radius:16px;overflow:hidden;margin-bottom:20px;box-shadow:0 4px 15px rgba(0,0,0,.08);display:flex;flex-wrap:wrap;transition:.2s}
.post-card:hover{box-shadow:0 6px 20px rgba(0,0,0,.12)}
.post-card .arte{width:280px;min-height:280px;background:#f5e6d0;display:flex;align-items:center;justify-content:center;overflow:hidden}
.post-card .arte img{width:100%;height:100%;object-fit:cover}
.post-card .arte .no-img{color:#a08060;font-size:.9rem;text-align:center;padding:20px}
.post-card .info{flex:1;padding:20px;min-width:280px}
.post-card .info h3{margin-bottom:8px;color:#8B4513}
.post-card .legenda{background:#fdf6ee;padding:12px;border-radius:10px;margin:10px 0;font-size:.9rem;line-height:1.5;white-space:pre-wrap;max-height:200px;overflow-y:auto}
.post-card .meta{font-size:.8rem;color:#a08060;margin-bottom:12px}
.post-card .actions{display:flex;gap:8px;flex-wrap:wrap}
.btn{padding:10px 18px;border:none;border-radius:10px;cursor:pointer;font-weight:600;font-size:.85rem;transition:.2s}
.btn:hover{transform:translateY(-1px)}
.btn-approve{background:#27ae60;color:white}
.btn-reject{background:#e74c3c;color:white}
.btn-recreate{background:#f39c12;color:white}
.btn-approve:hover{background:#219a52}
.btn-reject:hover{background:#c0392b}
.btn-recreate:hover{background:#d68910}
.status-badge{display:inline-block;padding:4px 12px;border-radius:15px;font-size:.75rem;font-weight:600;text-transform:uppercase}
.status-pending{background:#fff3cd;color:#856404}
.status-published{background:#d4edda;color:#155724}
.status-rejected{background:#f8d7da;color:#721c24}
.status-recreated{background:#d1ecf1;color:#0c5460}
.status-draft{background:#e2e3e5;color:#383d41}
.modal-overlay{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.5);z-index:100;align-items:center;justify-content:center}
.modal-overlay.show{display:flex}
.modal{background:white;border-radius:16px;padding:30px;max-width:500px;width:90%;max-height:80vh;overflow-y:auto}
.modal h2{margin-bottom:15px;color:#8B4513}
.modal textarea{width:100%;height:80px;border:2px solid #f0e0cc;border-radius:10px;padding:10px;font-size:.9rem;resize:vertical}
.modal .actions{margin-top:15px;display:flex;gap:10px;justify-content:flex-end}
.empty{text-align:center;padding:60px;color:#a08060}
.loading{text-align:center;padding:40px;color:#a08060}
@media(max-width:600px){.post-card .arte{width:100%;height:200px}}
</style>
</head>
<body>
<div class="header">
  <h1>Delicias da Vovo Teté — Painel</h1>
  <div style="display:flex;align-items:center;gap:20px">
    <div class="stats" id="stats"></div>
    <a href="/politica-privacidade" target="_blank" style="color:white;text-decoration:none;font-size:.85rem;background:rgba(255,255,255,.2);padding:6px 14px;border-radius:20px">Politica de Privacidade</a>
  </div>
</div>
<div class="container">
  <div class="tabs">
    <button class="tab active" onclick="loadPosts('pending')">Pendentes</button>
    <button class="tab" onclick="loadPosts('published')">Publicados</button>
    <button class="tab" onclick="loadPosts('rejected')">Rejeitados</button>
    <button class="tab" onclick="loadPosts('')">Todos</button>
    <button class="tab" onclick="loadMessages()">Mensagens</button>
  </div>
  <div id="content"><div class="loading">A carregar...</div></div>
</div>
<div class="modal-overlay" id="rejectModal">
  <div class="modal">
    <h2>Rejeitar Post</h2>
    <p>Motivo (opcional):</p>
    <textarea id="rejectReason"></textarea>
    <div class="actions">
      <button class="btn btn-reject" onclick="confirmReject()">Rejeitar</button>
      <button class="btn" onclick="closeModal()" style="background:#eee">Cancelar</button>
    </div>
  </div>
</div>
<script>
const TOKEN = new URLSearchParams(window.location.search).get('token') || '';
let currentRejectId = null;

async function api(url, opts = {}) {
  const sep = url.includes('?') ? '&' : '?';
  const r = await fetch(url + sep + 'token=' + TOKEN, opts);
  return r.json();
}

async function loadStats() {
  const s = await api('/api/stats');
  document.getElementById('stats').innerHTML =
    '<span>Pendentes: ' + s.pending + '</span>' +
    '<span>Publicados: ' + s.published + '</span>' +
    '<span>Rejeitados: ' + s.rejected + '</span>' +
    '<span>Mensagens: ' + s.total_messages + '</span>';
}

async function loadPosts(status) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  event.target.classList.add('active');
  document.getElementById('content').innerHTML = '<div class="loading">A carregar...</div>';
  const posts = await api('/api/posts' + (status ? '?status=' + status : ''));
  if (!posts.length) {
    document.getElementById('content').innerHTML = '<div class="empty">Nenhum post encontrado.</div>';
    return;
  }
  document.getElementById('content').innerHTML = posts.map(p => `
    <div class="post-card" id="post-${p.id}">
      <div class="arte">
        ${p.arte_path ? '<img src="/artes/' + p.arte_path.split('/').pop() + '" alt="arte">' : '<div class="no-img">Sem imagem</div>'}
      </div>
      <div class="info">
        <h3><span class="status-badge status-${p.status}">${p.status}</span> ${p.tema || ''}</h3>
        <div class="meta">ID: ${p.id} | Criado: ${p.created_at ? p.created_at.slice(0,16).replace('T',' ') : '-'} | Recriacoes: ${p.recreate_count || 0}</div>
        <div class="legenda">${p.legenda || 'Sem legenda'}</div>
        ${p.rejected_reason ? '<div class="meta" style="color:#e74c3c">Motivo: ' + p.rejected_reason + '</div>' : ''}
        ${p.status === 'pending' ? `
        <div class="actions">
          <button class="btn btn-approve" onclick="approvePost('${p.id}')">Publicar</button>
          <button class="btn btn-reject" onclick="openReject('${p.id}')">Rejeitar</button>
          <button class="btn btn-recreate" onclick="recreatePost('${p.id}')">Recriar</button>
        </div>` : ''}
      </div>
    </div>
  `).join('');
  loadStats();
}

async function approvePost(id) {
  if (!confirm('Publicar este post no Facebook?')) return;
  const r = await api('/api/posts/' + id + '/approve', {method:'POST'});
  if (r.ok) { alert('Publicado!'); loadPosts('pending'); } else { alert('Erro: ' + (r.error || 'desconhecido')); }
}

function openReject(id) { currentRejectId = id; document.getElementById('rejectModal').classList.add('show'); }
function closeModal() { document.getElementById('rejectModal').classList.remove('show'); currentRejectId = null; }

async function confirmReject() {
  if (!currentRejectId) return;
  const reason = document.getElementById('rejectReason').value;
  await api('/api/posts/' + currentRejectId + '/reject', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({reason})});
  closeModal(); document.getElementById('rejectReason').value = '';
  loadPosts('pending');
}

async function recreatePost(id) {
  if (!confirm('Gerar nova legenda + arte?')) return;
  const r = await api('/api/posts/' + id + '/recreate', {method:'POST'});
  if (r.ok) { alert('Novo post criado: ' + r.new_post_id); loadPosts('pending'); } else { alert('Erro: ' + (r.error || 'desconhecido')); }
}

async function loadMessages() {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  event.target.classList.add('active');
  document.getElementById('content').innerHTML = '<div class="loading">A carregar...</div>';
  const msgs = await api('/api/messages?limit=30');
  if (!msgs.length) {
    document.getElementById('content').innerHTML = '<div class="empty">Nenhuma mensagem ainda.</div>';
    return;
  }
  document.getElementById('content').innerHTML = '<div style="display:flex;flex-direction:column;gap:12px">' + msgs.map(m => `
    <div style="background:white;border-radius:12px;padding:16px;box-shadow:0 2px 8px rgba(0,0,0,.06)">
      <div style="display:flex;justify-content:space-between;margin-bottom:8px">
        <strong style="color:#8B4513">${m.psid || 'anon'}</strong>
        <span style="color:#a08060;font-size:.8rem">${m.received_at ? m.received_at.slice(0,16).replace('T',' ') : ''}</span>
      </div>
      <div style="background:#fdf6ee;padding:10px;border-radius:8px;margin-bottom:6px"><strong>Recebi:</strong> ${m.text || ''}</div>
      <div style="background:#e8f5e9;padding:10px;border-radius:8px"><strong>Resposta:</strong> ${m.reply || ''}</div>
    </div>
  `).join('') + '</div>';
  loadStats();
}

loadStats(); loadPosts('pending');
</script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
