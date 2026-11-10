import sqlite3
import os
import wave
import subprocess
from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

app = FastAPI()
templates = Jinja2Templates(directory="templates")

DB_PATH = "/root/my_web_panel/database.db"
UPLOAD_DIR = "/root/sipp_clean/sipp/audio"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS server_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            server_ip TEXT,
            sender_ext TEXT,
            target_company TEXT,
            target_ext TEXT,
            password TEXT,
            audio TEXT,
            schedule TEXT
        )
    """)
    conn.commit()
    
    # اگر جدول خالی بود، 3 کارت پیش‌فرض بساز
    cursor.execute("SELECT COUNT(*) FROM server_configs")
    if cursor.fetchone()[0] == 0:
        default_configs = [
            ("Server 1 (Main Kerio)", "192.168.81.10", "308", "Company B", "307", "******", f"{UPLOAD_DIR}/audio_1.wav", "16:35"),
            ("Server 2 (Sub Test)", "192.168.81.10", "308", "Company C", "306", "******", f"{UPLOAD_DIR}/audio_2.wav", "12:26"),
            ("Server 3 (Backup)", "192.168.81.10", "308", "Company D", "307", "******", f"{UPLOAD_DIR}/announcement.wav", "18:00")
        ]
        cursor.executemany("""
            INSERT INTO server_configs (name, server_ip, sender_ext, target_company, target_ext, password, audio, schedule)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, default_configs)
        conn.commit()
    conn.close()

init_db()

def get_wav_duration_ms(wav_path):
    try:
        if os.path.exists(wav_path):
            with wave.open(wav_path, 'r') as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                return int((frames / float(rate)) * 1000)
    except Exception:
        pass
    return 15000  # پیش‌فرض 15 ثانیه


@app.get("/")
async def index(request: Request, message: str = None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM server_configs")
    rows = cursor.fetchall()
    conn.close()
    
    configs = {row["id"]: dict(row) for row in rows}
    
    # اصلاح نحوه صدا زدن TemplateResponse با توجه به نسخه جدید استارلت
    return templates.TemplateResponse(request, "index.html", {"configs": configs, "message": message})

@app.post("/update/{sc_id}")
async def update_config(
    sc_id: int,
    server_ip: str = Form(...),
    sender_ext: str = Form(...),
    target_company: str = Form(...),
    target_ext: str = Form(...),
    password: str = Form(...),
    schedule: str = Form(...),
    audio_file: UploadFile = File(None)
):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    audio_path = None
    if audio_file and audio_file.filename:
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        audio_path = os.path.join(UPLOAD_DIR, audio_file.filename)
        with open(audio_path, "wb") as buffer:
            buffer.write(await audio_file.read())
        
        cursor.execute("UPDATE server_configs SET server_ip=?, sender_ext=?, target_company=?, target_ext=?, password=?, audio=?, schedule=? WHERE id=?",
                       (server_ip, sender_ext, target_company, target_ext, password, audio_path, schedule, sc_id))
    else:
        cursor.execute("UPDATE server_configs SET server_ip=?, sender_ext=?, target_company=?, target_ext=?, password=?, schedule=? WHERE id=?",
                       (server_ip, sender_ext, target_company, target_ext, schedule, sc_id))
    conn.commit()
    conn.close()
    
    return RedirectResponse(url=f"/?message=Configuration+for+Server+{sc_id}+updated+successfully!", status_code=303)

@app.post("/run/{sc_id}")
async def run_test(sc_id: int, retry_count: int = 1):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM server_configs WHERE id = ?", (sc_id,))
    target = cursor.fetchone()
    conn.close()

    if not target:
        return RedirectResponse(url="/?message=Error:+Configuration+not+found", status_code=303)

    audio_path = target["audio"] if os.path.exists(target["audio"]) else f"{UPLOAD_DIR}/audio_1.wav"
    duration_ms = get_wav_duration_ms(audio_path)

    xml_template_path = "/root/my_web_panel/call_script_template.xml"
    xml_path = "/root/my_web_panel/call_script.xml"
    
    try:
        with open(xml_template_path, "r") as f:
            content = f.read()
            
        content = content.replace("DURATION_PLACEHOLDER", str(duration_ms))
        content = content.replace("AUDIO_PLACEHOLDER", audio_path)
        
        with open(xml_path, "w") as f:
            f.write(content)
    except Exception as e:
        return RedirectResponse(url=f"/?message=Template+error:+{str(e)}", status_code=303)

    log_path = f"/root/my_web_panel/sipp_error_{sc_id}.log"
    # اجرای تست به همراه مکانیزم تلاش مجدد (Retry Loop) در دستورات شل
    cmd = f"for i in {{1..{retry_count}}}; do /usr/local/bin/sipp {target['server_ip']} -s {target['target_ext']} -sf {xml_path} -p 5060 -r 1 -m 1 > {log_path} 2>&1; done &"
    
    try:
        subprocess.run(cmd, shell=True)
        result_msg = f"Test for {target['name']} started with {retry_count} retry attempt(s)!"
    except Exception as e:
        result_msg = f"Execution error: {str(e)}"

    return RedirectResponse(url=f"/?message={result_msg}", status_code=303)

@app.post("/stop/{sc_id}")
async def stop_test(sc_id: int):
    try:
        subprocess.run("pkill -9 -f sipp", shell=True)
        subprocess.run("killall -9 sipp > /dev/null 2>&1", shell=True)
        result_msg = "All running tests stopped successfully!"
    except Exception as e:
        result_msg = f"Error stopping test: {str(e)}"

    return RedirectResponse(url=f"/?message={result_msg}", status_code=303)
