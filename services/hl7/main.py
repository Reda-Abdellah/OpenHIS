import asyncio, logging, os, sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from database import init_db, get_db
from routers  import messages, send
import bus_consumer
import log_config

log_config.configure("hl7")
log = logging.getLogger('hl7')

ROOT_PATH = os.environ.get('ROOT_PATH', '')

_REQUIRED_ENV = ["KEYCLOAK_TOKEN_URL", "KEYCLOAK_CLIENT_ID", "KEYCLOAK_CLIENT_SECRET"]


def _check_env() -> None:
    missing = [k for k in _REQUIRED_ENV if not os.getenv(k)]
    if missing:
        sys.exit(f"FATAL: Missing required env vars: {', '.join(missing)}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_env()
    init_db()
    bus_task = asyncio.create_task(bus_consumer.consume_loop())
    mllp_server = None
    if os.environ.get('MLLP_ENABLED', 'true').lower() == 'true':
        host = os.environ.get('MLLP_HOST', '0.0.0.0')
        port = int(os.environ.get('MLLP_PORT', '2575'))
        from mllp     import start_server
        from handlers import dispatch
        mllp_server = await start_server(host, port, dispatch)
        log.info(f"MLLP ready on {host}:{port}")
    log.info("HL7 Gateway v1.0 ready")
    yield
    bus_task.cancel()
    try:
        await bus_task
    except asyncio.CancelledError:
        pass
    if mllp_server:
        mllp_server.close()
        await mllp_server.wait_closed()


from jwt_auth import JWTMiddleware

app = FastAPI(title="HL7 v2 Gateway", version="1.0.0", root_path=ROOT_PATH, lifespan=lifespan)
app.add_middleware(JWTMiddleware)
app.include_router(messages.router)
app.include_router(send.router)

STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')


@app.get('/', response_class=HTMLResponse)
async def index():
    with open(os.path.join(STATIC_DIR, 'index.html'), encoding='utf-8') as f:
        return f.read()


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
