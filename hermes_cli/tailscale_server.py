
"""
Hermes Tailscale Server - JSON-RPC 2.0 over HTTP + WebSocket on Tailscale IP.

Usage: hermes serve --port 8787 --tailscale
"""
from __future__ import annotations
import asyncio, json, logging, os, re, shutil, subprocess, sys, time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

def is_tailscale_installed() -> bool:
    return shutil.which("tailscale") is not None

def get_tailscale_ip() -> Optional[str]:
    try:
        r = subprocess.run(["tailscale","ip","-4"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            ip = r.stdout.strip()
            if ip.startswith("100."): return ip
    except Exception: pass
    return None

def get_tailscale_status() -> dict:
    result = {"running":False,"ip":None,"hostname":None,"peers":[]}
    try:
        r = subprocess.run(["tailscale","status","--json"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            data = json.loads(r.stdout)
            result["running"] = True
            result["hostname"] = data.get("Self",{}).get("HostName","")
            result["ip"] = get_tailscale_ip()
            for pid, p in data.get("Peer",{}).items():
                result["peers"].append({"hostname":p.get("HostName",pid),"ip":p.get("TailscaleIPs",[None])[0],"online":p.get("Online",False)})
    except Exception: pass
    return result

class HermesRpcHandler:
    def __init__(self): self._started = time.time()

    async def handle(self, method: str, params: dict | None = None) -> Any:
        params = params or {}
        if method == "chat/send":
            return await self._chat(params.get("message",""))
        if method == "status/get":
            return await self._status()
        if method == "insights/list":
            return await self._insights(params.get("limit",10))
        if method == "project/switch":
            return await self._switch(params.get("name",""))
        if method == "code/ask":
            return await self._ask(params.get("code",""), params.get("question",""))
        if method == "code/improve":
            return await self._improve(params.get("code",""))
        if method == "code/generateTests":
            return await self._tests(params.get("code",""))
        if method == "tester/run":
            return await self._tester(params.get("file",""))
        if method == "initialize":
            return {"server":"hermes-tailscale","version":"1.0.0","uptime":time.time()-self._started,"tailscale":get_tailscale_status()}
        raise ValueError(f"Unknown method: {method}")

    async def _chat(self, msg: str) -> dict:
        if not msg: return {"text": "No message."}
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "hermes_cli.main", "chat", "-q", msg, "-Q",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "HERMES_SOURCE": "tailscale"},
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
            return {"text": out.decode("utf-8", errors="replace").strip()}
        except Exception as e:
            return {"text": f"Error: {e}"}

    async def _status(self) -> dict:
        ts = get_tailscale_status()
        return {"server":"hermes-tailscale","uptime":time.time()-self._started,"tailscale":ts,"project":"","activeWorkflows":0,"insightsCount":0}

    async def _insights(self, limit: int) -> dict:
        try:
            from projects.project_insights import get_project_insights
            pi = get_project_insights()
            if pi:
                items = pi.list_all(limit=limit)
                return {"insights": [{"id":i.id,"text":i.text,"importance":i.importance,"tags":i.tags,"source":i.source} for i in items]}
        except Exception: pass
        return {"insights":[]}

    async def _switch(self, name: str) -> dict:
        if not name: return {"error":"Project name required"}
        try:
            from projects.project_manager import ProjectManager
            ProjectManager().switch_project(name)
            return {"switched":name}
        except Exception as e: return {"error":str(e)}

    async def _ask(self, code: str, question: str) -> dict:
        if not code: return {"answer":""}
        prompt = f"Code:\n```\n{code[:4000]}\n```\n\nQuestion: {question}\nAnswer concisely."
        r = await self._chat(prompt)
        return {"answer": r.get("text","")}

    async def _improve(self, code: str) -> dict:
        if not code: return {"improved":""}
        prompt = f"Improve this code. Fix bugs, add types, improve perf. Return ONLY the improved code.\n\n```\n{code[:4000]}\n```"
        r = await self._chat(prompt)
        text = r.get("text","")
        m = re.search(r"```(?:\w+)?\n(.*?)```", text, re.DOTALL)
        return {"improved": m.group(1).strip() if m else text}

    async def _tests(self, code: str) -> dict:
        if not code: return {"tests":""}
        prompt = f"Generate pytest tests for this code. Return ONLY the test code.\n\n```\n{code[:4000]}\n```"
        r = await self._chat(prompt)
        text = r.get("text","")
        m = re.search(r"```(?:\w+)?\n(.*?)```", text, re.DOTALL)
        return {"tests": m.group(1).strip() if m else text}

    async def _tester(self, file_path: str) -> dict:
        if not file_path: return {"error":"File path required"}
        try:
            r = subprocess.run(["python3","-m","pytest",file_path,"-v","--tb=short"], capture_output=True, text=True, timeout=60)
            return {"passed":r.returncode==0,"output":r.stdout[-3000:],"exitCode":r.returncode}
        except Exception as e: return {"error":str(e)}

async def _handle_jsonrpc(request):
    from aiohttp import web
    try: body = await request.json()
    except: return web.json_response({"jsonrpc":"2.0","error":{"code":-32700,"message":"Parse error"},"id":None}, status=400)
    rid = body.get("id")
    try:
        result = await request.app["hermes_rpc"].handle(body.get("method",""), body.get("params",{}))
        return web.json_response({"jsonrpc":"2.0","result":result,"id":rid})
    except ValueError as e:
        return web.json_response({"jsonrpc":"2.0","error":{"code":-32601,"message":str(e)},"id":rid})
    except Exception as e:
        return web.json_response({"jsonrpc":"2.0","error":{"code":-32603,"message":str(e)},"id":rid}, status=500)

async def _handle_ws(request):
    from aiohttp import web
    ws = web.WebSocketResponse(); await ws.prepare(request)
    h = request.app["hermes_rpc"]
    async for msg in ws:
        if msg.type == web.WSMsgType.TEXT:
            try:
                body = json.loads(msg.data)
                result = await h.handle(body.get("method",""), body.get("params",{}))
                await ws.send_json({"jsonrpc":"2.0","result":result,"id":body.get("id")})
            except Exception as e:
                await ws.send_json({"jsonrpc":"2.0","error":{"code":-32603,"message":str(e)},"id":body.get("id") if isinstance(body,dict) else None})
        elif msg.type == web.WSMsgType.ERROR:
            logger.error("WS error: %s", ws.exception())
    return ws

async def _handle_health(request):
    from aiohttp import web
    return web.json_response({"status":"ok","tailscale":get_tailscale_ip()})

def start_tailscale_server(port: int = 8787) -> None:
    try: from aiohttp import web
    except ImportError:
        print("Error: aiohttp required. pip install aiohttp"); sys.exit(1)
    ts_ip = get_tailscale_ip()
    host = ts_ip if ts_ip else "127.0.0.1"
    if ts_ip:
        print(f"Tailscale: {ts_ip}")
        s = get_tailscale_status()
        print(f"  Hostname: {s.get('hostname','?')}, Peers online: {sum(1 for p in s.get('peers',[]) if p.get('online'))}")
    else:
        print("Warning: Tailscale not detected. Listening on 127.0.0.1 (local only).")
    app = web.Application()
    app["hermes_rpc"] = HermesRpcHandler()
    app.router.add_post("/api/jsonrpc", _handle_jsonrpc)
    app.router.add_get("/api/ws", _handle_ws)
    app.router.add_get("/health", _handle_health)
    print(f"\nHermes Server: http://{host}:{port}")
    print(f"  JSON-RPC: POST /api/jsonrpc")
    print(f"  WebSocket: ws://{host}:{port}/api/ws")
    print(f"  Health: GET /health\n")
    web.run_app(app, host=host, port=port, print=None)
