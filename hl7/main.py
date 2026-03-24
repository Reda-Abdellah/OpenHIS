import asyncio, logging, os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from database import init_db, get_db
from routers  import messages, send

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('hl7')

ROOT_PATH = os.environ.get('ROOT_PATH', '')
app = FastAPI(title="HL7 v2 Gateway", version="1.0.0", root_path=ROOT_PATH)
app.include_router(messages.router)
app.include_router(send.router)

STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')


@app.get('/', response_class=HTMLResponse)
async def index():
    with open(os.path.join(STATIC_DIR, 'index.html'), encoding='utf-8') as f:
        return f.read()


@app.on_event('startup')
async def startup():
    init_db()
    if os.environ.get('MLLP_ENABLED', 'true').lower() == 'true':
        host = os.environ.get('MLLP_HOST', '0.0.0.0')
        port = int(os.environ.get('MLLP_PORT', '2575'))
        from mllp     import start_server
        from handlers import dispatch
        server = await start_server(host, port, dispatch)
        app.state.mllp_server = server
        log.info(f"MLLP ready on {host}:{port}")
    log.info("HL7 Gateway v1.0 ready")


@app.on_event('shutdown')
async def shutdown():
    srv = getattr(app.state, 'mllp_server', None)
    if srv:
        srv.close()
        await srv.wait_closed()


@app.get('/api/health')
def health():
    with get_db() as db:
        total    = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        inbound  = db.execute(
            "SELECT COUNT(*) FROM messages WHERE direction='inbound'").fetchone()[0]
        outbound = db.execute(
            "SELECT COUNT(*) FROM messages WHERE direction='outbound'").fetchone()[0]
        errors   = db.execute(
            "SELECT COUNT(*) FROM messages WHERE status='error'").fetchone()[0]
    mllp_en = os.environ.get('MLLP_ENABLED', 'true').lower() == 'true'
    return {
        "status":       "ok",
        "service":      "hl7",
        "version":      "1.0.0",
        "mllp_enabled": mllp_en,
        "mllp_port":    int(os.environ.get('MLLP_PORT', 2575)),
        "total":        total,
        "inbound":      inbound,
        "outbound":     outbound,
        "errors":       errors,
    }
