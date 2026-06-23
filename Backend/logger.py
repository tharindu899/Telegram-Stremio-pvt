import pytz
from logging import getLogger, FileHandler, StreamHandler, INFO, ERROR, Formatter, basicConfig
from datetime import datetime

IST = pytz.timezone("Asia/Kolkata")

class ISTFormatter(Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, IST)
        return dt.strftime(datefmt or "%d-%b-%y %I:%M:%S %p")

file_handler = FileHandler("log.txt")
stream_handler = StreamHandler()
formatter = ISTFormatter("[%(asctime)s] [%(levelname)s] - %(message)s", "%d-%b-%y %I:%M:%S %p")
file_handler.setFormatter(formatter)
stream_handler.setFormatter(formatter)

basicConfig(
    handlers=[file_handler, stream_handler],
    level=INFO
)

getLogger("httpx").setLevel(ERROR)
getLogger("pyrogram").setLevel(ERROR)
getLogger("fastapi").setLevel(ERROR)


LOGGER = getLogger(__name__)
LOGGER.setLevel(INFO)

LOGGER.info("Logger initialized with IST timezone.")
