import uvicorn
from Backend.config import Telegram
from Backend.fastapi.main import app


Port = Telegram.PORT
config = uvicorn.Config(
    app=app,
    host="0.0.0.0",
    port=Port,
    loop="none",       # FIX: don't replace the running asyncio loop with uvloop
    http="httptools",
    timeout_keep_alive=30,
    timeout_graceful_shutdown=5,
)
server = uvicorn.Server(config)
