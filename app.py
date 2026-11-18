import os, json, random, re, sqlite3
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, send_from_directory
from pypdf import PdfReader
import anthropic

app = Flask(__name__)
DB = "tesis.db"
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MODELO = "claude-sonnet-5-5"
MAX_SECCIONES = 15  # limita las llamadas a la IA al subir la tesis


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS secciones(
            id INTEGER PRIMARY KEY, texto TEXT)""")
        con.execute("""CREATE TABLE IF NOT EXISTS preguntas(
            id INTEGER PRIMARY KEY,
            texto TEXT,
            seccion_id INTEGER,
            origen TEXT DEFAULT 'ia',      -- 'ia' o 'profesor'
            nivel INTEGER DEFAULT 0,       -- dominio 0 a 5
            proxima TEXT)""")


def pedir_ia(prompt):
    r = client.messages.create(model=MODELO, max_tokens=800,
        messages=[{"role": "user", "content": prompt}])
    return r.content[0].text


def leer_json(texto):
    """La IA a veces envuelve el JSON en ```json ... ```; esto lo limpia."""
    m = re.search(r"(\[.*\]|\{.*\})", texto, re.S)
    return json.loads(m.group(1) if m else texto)


# 1. Subir la tesis y generar preguntas
@app.post("/subir")
def subir():
    pdf = PdfReader(request.files["tesis"])
    texto = "\n".join(p.extract_text() or "" for p in pdf.pages)
    tam = max(4000, len(texto) // MAX_SECCIONES + 1)
    trozos = [texto[i:i+tam] for i in range(0, len(texto), tam)]
    ahora = datetime.now().isoformat()
    total = 0
    with db() as con:
        for t in trozos:
            sid = con.execute("INSERT INTO secciones(texto) VALUES(?)", (t,)).lastrowid
            resp = pedir_ia(
                "Eres jurado de tesis. Lee este fragmento y escribe 3 preguntas "
                "difíciles que harías en la sustentación. Responde solo con una "
                "lista JSON de strings.\n\n" + t)
            for q in leer_json(resp):
                con.execute("INSERT INTO preguntas(texto, seccion_id, proxima) VALUES(?,?,?)",
                            (q, sid, ahora))
                total += 1
    return jsonify(ok=True, preguntas=total)


# 2. Agregar pregunta propia (del profesor)
@app.post("/pregunta")
def agregar_pregunta():
    q = request.json["texto"].strip()
    if not q:
        return jsonify(ok=False), 400
    with db() as con:
        con.execute("INSERT INTO preguntas(texto, origen, proxima) VALUES(?, 'profesor', ?)",
                    (q, datetime.now().isoformat()))
    return jsonify(ok=True)


# 3. Obtener la siguiente pregunta pendiente
@app.get("/siguiente")
def siguiente():
    ahora = datetime.now().isoformat()
    with db() as con:
        filas = con.execute("SELECT * FROM preguntas WHERE proxima <= ? ORDER BY nivel",
                            (ahora,)).fetchall()
        prox = con.execute("SELECT MIN(proxima) m FROM preguntas").fetchone()["m"]
    if not filas:
        return jsonify(pregunta=None, proxima=prox)
    # Las del profesor salen con más frecuencia
    prof = [f for f in filas if f["origen"] == "profesor"]
    f = random.choice(prof) if prof and random.random() < 0.4 else filas[0]
    return jsonify(id=f["id"], pregunta=f["texto"], origen=f["origen"])


# 4. Evaluar la respuesta y reprogramar
@app.post("/responder")
def responder():
    pid, respuesta = request.json["id"], request.json["respuesta"]
    with db() as con:
        p = con.execute("SELECT * FROM preguntas WHERE id=?", (pid,)).fetchone()
        contexto = ""
        if p["seccion_id"]:
            contexto = con.execute("SELECT texto FROM secciones WHERE id=?",
                                   (p["seccion_id"],)).fetchone()["texto"]
        evaluacion = leer_json(pedir_ia(
            "Eres jurado de tesis. Califica la respuesta del 1 al 5 y da "
            "retroalimentación breve y concreta en español. Si la nota es menor "
            "a 3, reformula la pregunta con otras palabras. Responde solo JSON "
            "con las claves nota, comentario, reformulada.\n\n"
            f"Contexto de la tesis: {contexto}\nPregunta: {p['texto']}\n"
            f"Respuesta del estudiante: {respuesta}"))
        nota = int(evaluacion["nota"])
        if nota < 3:
            espera = timedelta(minutes=random.randint(60, 90))
            nuevo_texto = evaluacion.get("reformulada") or p["texto"]
            nivel = 0
        else:
            nivel = min(p["nivel"] + 1, 5)
            espera = timedelta(days=max(1, 2 ** (nivel - 1)))
            nuevo_texto = p["texto"]
        con.execute("UPDATE preguntas SET texto=?, nivel=?, proxima=? WHERE id=?",
                    (nuevo_texto, nivel, (datetime.now() + espera).isoformat(), pid))
    evaluacion["vuelve_en"] = (f"{int(espera.total_seconds() // 60)} minutos"
                               if nota < 3 else f"{espera.days} día(s)")
    return jsonify(evaluacion)


@app.get("/")
def inicio():
    return render_template("index.html")


# El service worker debe servirse desde la raíz para que la app sea instalable
@app.get("/sw.js")
def service_worker():
    return send_from_directory("static", "sw.js", mimetype="application/javascript")


init_db()
if __name__ == "__main__":
    app.run(debug=True)
