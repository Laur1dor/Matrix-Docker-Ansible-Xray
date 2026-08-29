#!/usr/bin/env python3
"""
baibot LLM gateway — multi-provider free-LLM proxy.
Speaks BOTH OpenAI Chat-Completions (/v1/chat/completions) AND the Responses API
(/v1/responses, which baibot's `openai` provider uses — and which carries images).
Internally everything is run as Chat-Completions against:
  Groq (text) -> OpenRouter free pool (text + vision) -> friendly msg.

ALL upstream traffic goes through xray's SOCKS inbound (127.0.0.1:10808), which the
generated xray config always routes through the VLESS balancer (fallbackTag: direct).
Reason: both Groq and OpenRouter block/deny Russian egress IPs; OpenRouter answers
`403 Access denied by security policy` when reached directly. The HTTP inbound (10809)
is domain-list based and would leak these hosts out direct, so SOCKS is used instead.
Stdlib + curl (urllib can't tunnel HTTPS through a proxy). systemd, 0.0.0.0:8765.
"""
import json, sys, time, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def rd(p):
    try: return open(p).read().strip()
    except Exception: return ""
GROQ_KEY = rd("/root/.secrets/groq_key")
OR_KEY = rd("/root/.secrets/openrouter_key")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
OR_MODELS = "https://openrouter.ai/api/v1/models"
XRAY = "socks5h://127.0.0.1:10808"
LISTEN = ("0.0.0.0", 8765)
FRIENDLY = ("Извини, сейчас все бесплатные нейросети перегружены (исчерпаны лимиты). "
            "Попробуй, пожалуйста, ещё раз через минуту 🙏")
SKIP = ["lyria", "content-safety", "whisper", "tts", "embed", "image-gen",
        "stable-diffusion", "prompt-guard", "orpheus", "safeguard"]
# Groq retired llama-3.3-70b-versatile and llama-4-scout (404 model_not_found).
GROQ_TEXT = ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"]
GROQ_VIS = []  # Groq currently offers no vision model — images go to OpenRouter.

_cache = {"t": 0.0, "or_text": [], "or_vis": []}
def log(*a): print("[gw]", *a, file=sys.stderr, flush=True)

def curl(url, key, payload=None, timeout=75):
    """GET/POST through the xray SOCKS proxy. Returns (http_code, body_bytes)."""
    cmd = ["curl", "-s", "-x", XRAY, "-m", str(timeout - 15), "-w", "\n%{http_code}", url,
           "-H", "Authorization: Bearer " + key]
    if payload is not None:
        cmd += ["-X", "POST", "-H", "Content-Type: application/json",
                "-H", "HTTP-Referer: https://example.com", "-H", "X-Title: matrix-baibot",
                "--data-binary", "@-"]
    try:
        p = subprocess.run(cmd, input=payload, capture_output=True, timeout=timeout)
    except Exception:
        return 0, b""
    out = p.stdout; nl = out.rfind(b"\n")
    if nl < 0: return 0, out
    try: code = int(out[nl + 1:].strip() or 0)
    except ValueError: code = 0
    return code, out[:nl]

def refresh():
    if _cache["or_text"] and time.time() - _cache["t"] < 1800: return
    code, body = curl(OR_MODELS, OR_KEY, timeout=35)
    if code != 200 or not body:
        log("refresh: openrouter model list failed, code=", code, body[:160]); return
    try:
        d = json.loads(body)
    except Exception:
        log("refresh: bad json from openrouter"); return
    t = []; v = []
    for m in d.get("data", []):
        pr = m.get("pricing", {}); mid = m.get("id", "")
        if pr.get("prompt") != "0" or pr.get("completion") != "0": continue
        if any(s in mid for s in SKIP): continue
        t.append(mid)
        if "image" in m.get("architecture", {}).get("input_modalities", []): v.append(mid)
    # openrouter/free auto-routes across the whole free pool — best first pick.
    for lst in (t, v):
        if "openrouter/free" in lst:
            lst.remove("openrouter/free"); lst.insert(0, "openrouter/free")
    if t:
        _cache.update(t=time.time(), or_text=t, or_vis=v)
        log("refresh: openrouter free pool =", len(t), "text /", len(v), "vision")

def candidates(vision):
    refresh()
    # (provider, model): both providers go out through xray.
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

def content_of(raw):
    """Text of a chat-completion reply, or None if the answer is unusable."""
    try: m = json.loads(raw)["choices"][0]["message"]
    except Exception: return None
    c = m.get("content")
    if isinstance(c, list):  # some providers return content parts
        c = "".join(p.get("text", "") for p in c if isinstance(p, dict))
    return c.strip() if isinstance(c, str) and c.strip() else None

def chat_to_responses(raw, model):
    return responses_obj(content_of(raw) or "", model)

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
        code, raw = curl(url, key, json.dumps(payload).encode())
        if code == 200 and raw:
            j = None
            try: j = json.loads(raw)
            except Exception: pass
            # 200 + {"error": ...} or an empty answer (reasoning model that ran out of
            # tokens) is a failure — fall through to the next candidate instead of
            # handing baibot a blank message.
            if isinstance(j, dict) and not j.get("error") and content_of(raw) is not None:
                return 200, raw, model
            last = (429, raw); continue
        # Any non-200 (including 404 model_not_found, which is how a retired model
        # shows up) must NOT abort the chain — that is what killed the whole pool
        # when Groq dropped llama-3.3-70b-versatile.
        last = (code, raw)
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
            elif want_stream: self._s(200, sse(content_of(data) or ""), "text/event-stream")
            else: self._s(200, data)
        else:
            if is_resp: self._s(200, responses_obj(FRIENDLY, "gateway"))
            elif want_stream: self._s(200, sse(FRIENDLY), "text/event-stream")
            else: self._s(200, chatcomp(FRIENDLY))
    def log_message(self, *a): pass

if __name__ == "__main__":
    refresh()
    ThreadingHTTPServer(LISTEN, H).serve_forever()
