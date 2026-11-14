import sys

import uvicorn

from app.config.web_config import web_config

if __name__ == "__main__":
    kwargs = {}
    if sys.platform == "win32":
        kwargs["loop"] = "app.core.event_loop:loop_factory"
    uvicorn.run("app.api.app:app", host=web_config.host, port=web_config.port,
                forwarded_allow_ips=web_config.forwarded_allow_ip, **kwargs)