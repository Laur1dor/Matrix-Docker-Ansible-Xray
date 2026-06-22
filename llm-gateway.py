#!/usr/bin/env python3
"""
baibot LLM gateway — multi-provider free-LLM proxy.
Speaks BOTH OpenAI Chat-Completions (/v1/chat/completions) AND the Responses API
(/v1/responses, which baibot's `openai` provider uses — and which carries images).
Internally everything is run as Chat-Completions against:
  Groq (via xray, generous free limits, llama-4-scout for vision) -> OpenRouter free pool -> friendly msg.
Vision-aware. Stdlib + curl (urllib can't tunnel HTTPS through the proxy). systemd, 0.0.0.0:8765.
"""
import json, sys, time, subprocess, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def rd(p):
    try: return open(p).read().strip()
    except Exception: return ""
GROQ_KEY = rd("/root/.secrets/groq_key")
OR_KEY = rd("/root/.secrets/openrouter_key")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
OR_MODELS = "https://openrouter.ai/api/v1/models"
XRAY = "http://127.0.0.1:10809"
LISTEN = ("0.0.0.0", 8765)
FRIENDLY = ("Извини, сейчас все бесплатные нейросети перегружены (исчерпаны лимиты). "
            "Попробуй, пожалуйста, ещё раз через минуту 🙏")
RETRY = {429, 402, 403, 500, 502, 503, 520, 524}
SKIP = ["lyria", "content-safety", "whisper", "tts", "embed", "image-gen", "stable-diffusion"]
GROQ_TEXT = ["llama-3.3-70b-versatile", "meta-llama/llama-4-scout-17b-16e-instruct"]
GROQ_VIS = ["meta-llama/llama-4-scout-17b-16e-instruct"]

_cache = {"t": 0.0, "or_text": [], "or_vis": []}
def log(*a): print("[gw]", *a, file=sys.stderr, flush=True)

def refresh():
    if _cache["or_text"] and time.time() - _cache["t"] < 1800: return
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        d = json.loads(op.open(urllib.request.Request(OR_MODELS, headers={"Authorization": "Bearer " + OR_KEY}), timeout=20).read())
        t = []; v = []
        for m in d.get("data", []):
            pr = m.get("pricing", {}); mid = m.get("id", "")
            if pr.get("prompt") != "0" or pr.get("completion") != "0": continue
            if any(s in mid for s in SKIP): continue
            t.append(mid)
            if "image" in m.get("architecture", {}).get("input_modalities", []): v.append(mid)
        if t: _cache.update(t=time.time(), or_text=t, or_vis=v)
    except Exception: pass

def candidates(vision):
    refresh()
    # (provider, model): provider "groq" -> via xray; "or" -> direct
    if vision:
        return [("groq", m) for m in GROQ_VIS] + [("or", m) for m in _cache["or_vis"]]
    return [("groq", m) for m in GROQ_TEXT] + [("or", m) for m in _cache["or_text"]]

def msgs_have_image(messages):
    for msg in messages:
        c = msg.get("content")
        if isinstance(c, list):
            for p in c:
                if isinstance(p, dict) and p.get("type") in ("image_url", "input_image"): return True
    return False

# ---- Responses API <-> Chat Completions translation ----
def responses_to_chat(body):
    msgs = []
    instr = body.get("instructions")
    if instr: msgs.append({"role": "system", "content": instr})
    inp = body.get("input")
    if isinstance(inp, str):
        msgs.append({"role": "user", "content": inp})
    elif isinstance(inp, list):
        for item in inp:
            if not isinstance(item, dict): continue
            role = item.get("role", "user"); content = item.get("content")
            if isinstance(content, str):
                msgs.append({"role": role, "content": content})
            elif isinstance(content, list):
                parts = []
                for p in content:
                    t = p.get("type")
                    if t in ("input_text", "text", "output_text"):
                        parts.append({"type": "text", "text": p.get("text", "")})
                    elif t in ("input_image", "image_url"):
                        u = p.get("image_url")
                        if isinstance(u, dict): u = u.get("url")
                        if u: parts.append({"type": "image_url", "image_url": {"url": u}})
                msgs.append({"role": role, "content": parts or ""})
    chat = {"messages": msgs}
    if body.get("max_output_tokens"): chat["max_tokens"] = body["max_output_tokens"]
    if body.get("temperature") is not None: chat["temperature"] = body["temperature"]
    return chat

def chat_to_responses(raw, model):
    try: text = json.loads(raw)["choices"][0]["message"].get("content", "") or ""
    except Exception: text = ""
    return responses_obj(text, model)

def responses_obj(text, model):
    return json.dumps({"id": "resp_gw", "object": "response", "created_at": int(time.time()),
        "model": model or "gateway", "status": "completed",
        "output": [{"type": "message", "id": "msg_gw", "status": "completed", "role": "assistant",
                    "content": [{"type": "output_text", "text": text, "annotations": []}]}],
        "usage": {"input_tokens": 0, "input_tokens_details": {"cached_tokens": 0},
                  "output_tokens": 0, "output_tokens_details": {"reasoning_tokens": 0},
                  "total_tokens": 0}}).encode()

def try_all(chatbody, cands):
    payload = dict(chatbody); payload["stream"] = False
    last = (503, None)
    for (prov, model) in cands:
        payload["model"] = model
        url = GROQ_URL if prov == "groq" else OR_URL
        key = GROQ_KEY if prov == "groq" else OR_KEY
        cmd = ["curl", "-s", "-m", "60", "-w", "\n%{http_code}", "-X", "POST", url,
               "-H", "Authorization: Bearer " + key, "-H", "Content-Type: application/json",
               "-H", "HTTP-Referer: https://example.com", "-H", "X-Title: matrix-baibot", "--data-binary", "@-"]
        if prov == "groq": cmd[1:1] = ["-x", XRAY]
        try:
            p = subprocess.run(cmd, input=json.dumps(payload).encode(), capture_output=True, timeout=75)
            out = p.stdout; nl = out.rfind(b"\n")
            raw = out[:nl] if nl >= 0 else out
            code = int(out[nl+1:].strip() or 0) if nl >= 0 else 0
            if code == 200 and raw:
                j = json.loads(raw)
                if isinstance(j, dict) and j.get("error"): last = (429, raw); continue
                return 200, raw, model
            last = (code, raw)
            if code in RETRY or code == 0: continue
            return code, raw, model
        except Exception:
            last = (503, None); continue
    return last[0], last[1], None

def sse(text):
    ch = {"id": "gw", "object": "chat.completion.chunk", "created": int(time.time()), "model": "gateway",
          "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}
    return ("data: " + json.dumps(ch) + "\n\ndata: [DONE]\n\n").encode()

def chatcomp(text):
    return json.dumps({"id": "gw-fb", "object": "chat.completion", "created": int(time.time()), "model": "gateway",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}).encode()

class H(BaseHTTPRequestHandler):
    def _s(self, code, data, ctype="application/json"):
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            refresh()
            self._s(200, json.dumps({"object": "list", "data": [{"id": m, "object": "model"} for m in (GROQ_TEXT + _cache["or_text"])]}).encode())
        else:
            self._s(200, b'{"status":"ok"}')
    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0)); raw = self.rfile.read(ln)
        try: body = json.loads(raw)
        except Exception: self._s(400, b'{"error":"bad json"}'); return
        is_resp = self.path.rstrip("/").endswith("/responses")
        chatbody = responses_to_chat(body) if is_resp else body
        want_stream = (not is_resp) and bool(body.get("stream"))
        img = msgs_have_image(chatbody.get("messages", []))
        cands = candidates(img)
        code, data, used = try_all(chatbody, cands)
        log("POST", self.path, "resp_api=", is_resp, "img=", img, "cands=", len(cands), "-> code=", code, "used=", used)
        if code == 200 and data:
            if is_resp: self._s(200, chat_to_responses(data, used))
            elif want_stream:
                try: c = json.loads(data)["choices"][0]["message"].get("content", "")
                except Exception: c = ""
                self._s(200, sse(c), "text/event-stream")
            else: self._s(200, data)
        else:
            if is_resp: self._s(200, responses_obj(FRIENDLY, "gateway"))
            elif want_stream: self._s(200, sse(FRIENDLY), "text/event-stream")
            else: self._s(200, chatcomp(FRIENDLY))
    def log_message(self, *a): pass

if __name__ == "__main__":
    refresh()
    ThreadingHTTPServer(LISTEN, H).serve_forever()
