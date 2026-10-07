#!/usr/bin/env python3
"""
baibot LLM gateway — multi-provider free-LLM proxy.
Speaks BOTH OpenAI Chat-Completions (/v1/chat/completions) AND the Responses API
(/v1/responses, which baibot's `openai` provider uses — and which carries images).
Internally everything is run as Chat-Completions against:
  Groq active models + OpenRouter free pool; failures return honest HTTP 503.
Requests share a 45-second deadline, with at most six alternating model attempts.

ALL upstream traffic goes through xray's SOCKS inbound (127.0.0.1:10808), which the
generated xray config always routes through the VLESS balancer (fallbackTag: direct).
Reason: both Groq and OpenRouter block/deny Russian egress IPs; OpenRouter answers
`403 Access denied by security policy` when reached directly. The HTTP inbound (10809)
is domain-list based and would leak these hosts out direct, so SOCKS is used instead.
Stdlib + curl (urllib can't tunnel HTTPS through a proxy). systemd, 0.0.0.0:8765.
"""
import json, sys, time, subprocess, threading, itertools, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def rd(p):
    try: return open(p).read().strip()
    except Exception: return ""
GROQ_KEY = rd("/root/.secrets/groq_key")
OR_KEY = rd("/root/.secrets/openrouter_key")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
GROQ_MODELS = "https://api.groq.com/openai/v1/models"
OR_MODELS = "https://openrouter.ai/api/v1/models"
XRAY = "socks5h://127.0.0.1:10808"
LISTEN = ("0.0.0.0", 8765)
SKIP = ["lyria", "content-safety", "whisper", "tts", "embed", "image-gen",
        "stable-diffusion", "prompt-guard", "orpheus", "safeguard"]
# Bootstrap only; refresh discovers active models from each provider.
GROQ_TEXT = ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"]
GROQ_VIS = []  # Capability-aware discovery fills the live vision pool.

REQUEST_BUDGET = 45
MAX_ATTEMPTS = 6
_cache = {"groq_text": GROQ_TEXT[:], "groq_vis": [], "or_text": [], "or_vis": []}
_refresh_at = {"groq": 0.0, "or": 0.0}
_cache_lock = threading.Lock()
_cooldown = {}
_health = {"last_success": None, "last_error": None}
_provider_failures = {}
_recovery_lock = threading.Lock()
RECOVERY_STATE = "/root/.cache/llm-gateway-recovery-at"
try: _last_recovery = float(rd(RECOVERY_STATE) or 0)
except ValueError: _last_recovery = time.time()


def recover_proxy(deadline):
    """One request-driven, cooldown-limited refresh; reserve time for retry/rollback."""
    global _last_recovery
    if not all(_provider_failures.get(p) in (0, 403) for p in ("groq", "or")): return False
    if deadline - time.monotonic() < 26 or time.time() - _last_recovery < 600: return False
    if not os.path.isfile("/usr/local/bin/xray-manage") or not os.path.isfile("/usr/local/etc/xray/swap/sub.url"): return False
    if not _recovery_lock.acquire(blocking=False): return False
    try:
        if time.time() - _last_recovery < 600: return False
        _last_recovery = time.time()
        try:
            os.makedirs(os.path.dirname(RECOVERY_STATE), mode=0o700, exist_ok=True)
            with open(RECOVERY_STATE + ".new", "w") as state: state.write(str(_last_recovery))
            os.chmod(RECOVERY_STATE + ".new", 0o600)
            os.replace(RECOVERY_STATE + ".new", RECOVERY_STATE)
        except OSError:
            log("recovery disabled: cannot persist cooldown"); return False
        log("recovery: refreshing saved Xray subscription")
        try:
            result = subprocess.run(["/usr/local/bin/xray-manage", "refresh", "--timeout", "20"],
                                    capture_output=True, timeout=22)
        except (OSError, subprocess.TimeoutExpired):
            log("recovery: helper failed or timed out"); return False
        if result.returncode:
            log("recovery: helper returned", result.returncode); return False
        _refresh_at.update({"groq": 0.0, "or": 0.0})
        _provider_failures.clear()
        log("recovery: proxy restored")
        return True
    finally:
        _recovery_lock.release()
def log(*a): print("[gw]", *a, file=sys.stderr, flush=True)

def curl(url, key, payload=None, timeout=15):
    """GET/POST through the xray SOCKS proxy. Returns (http_code, body_bytes)."""
    cmd = ["curl", "-s", "-x", XRAY, "-m", str(max(0.1, timeout - 0.2)), "-w", "\n%{http_code}", url,
           "--connect-timeout", "5", "-H", "Authorization: Bearer " + key]
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

def refresh(deadline=None):
    """Keep last good pool on failure; retry failures after 60s, successes after 30m."""
    deadline = deadline or time.monotonic() + 8
    if not _cache_lock.acquire(blocking=False): return
    try:
        for prov, url, key in (("groq", GROQ_MODELS, GROQ_KEY), ("or", OR_MODELS, OR_KEY)):
            now = time.monotonic()
            if not key or now < _refresh_at[prov] or deadline - now < 0.5: continue
            _refresh_at[prov] = now + 60
            code, body = curl(url, key, timeout=min(4, deadline - now))
            if code != 200:
                _provider_failures[prov] = code
                log("discovery", prov, "http=", code); continue
            _provider_failures.pop(prov, None)
            try:
                models = json.loads(body)["data"]
                text, vision = [], []
                for m in models:
                    mid = m.get("id", "")
                    if not mid or m.get("active") is False or any(x in mid for x in SKIP): continue
                    arch = m.get("architecture", {})
                    inputs = arch.get("input_modalities", m.get("input_modalities", []))
                    outputs = arch.get("output_modalities", m.get("output_modalities", []))
                    if prov == "or":
                        pricing = m.get("pricing", {})
                        if str(pricing.get("prompt")) != "0" or str(pricing.get("completion")) != "0": continue
                    if outputs and "text" not in outputs: continue
                    text.append(mid)
                    if "image" in inputs: vision.append(mid)
                if not text: continue
                preferred = GROQ_TEXT if prov == "groq" else ["openrouter/free"]
                text.sort(key=lambda m: (preferred.index(m) if m in preferred else len(preferred), m))
                _cache[prov + "_text"] = text
                _cache[prov + "_vis"] = vision
                _refresh_at[prov] = now + 1800
                log("discovery", prov, "text=", len(text), "vision=", len(vision))
            except (ValueError, KeyError, TypeError, AttributeError):
                log("discovery", prov, "invalid response")
    finally:
        _cache_lock.release()

def candidates(vision, deadline=None):
    refresh(deadline)
    kind = "_vis" if vision else "_text"
    pools = [[(p, m) for m in _cache[p + kind]
              if key and _cooldown.get((p, m), 0) < time.monotonic()]
             for p, key in (("groq", GROQ_KEY), ("or", OR_KEY))]
    return [candidate for pair in itertools.zip_longest(*pools) for candidate in pair if candidate]

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

def try_all(chatbody, cands, deadline=None):
    deadline = deadline or time.monotonic() + REQUEST_BUDGET
    payload = dict(chatbody); payload["stream"] = False
    last = (503, None)
    blocked = set()
    attempts = 0
    for prov, model in cands:
        remaining = deadline - time.monotonic()
        if remaining < 0.5 or attempts >= MAX_ATTEMPTS: break
        if prov in blocked: continue
        attempts += 1
        payload["model"] = model
        url = GROQ_URL if prov == "groq" else OR_URL
        key = GROQ_KEY if prov == "groq" else OR_KEY
        code, raw = curl(url, key, json.dumps(payload).encode(), timeout=min(15, remaining))
        log("attempt", prov, model, "http=", code)
        if code == 200 and raw and content_of(raw) is not None:
            _provider_failures.pop(prov, None)
            _health.update(last_success=int(time.time()), last_error=None)
            return 200, raw, model
        last = (code or 503, None)
        _provider_failures[prov] = code
        if code in (0, 401, 403): blocked.add(prov)
        if code in (404, 429): _cooldown[(prov, model)] = time.monotonic() + 60
    _health["last_error"] = {"http_status": last[0], "at": int(time.time())}
    # Never claim rate limits for network, authorization, or geography failures.
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
            self._s(200, json.dumps({"object": "list", "data": [{"id": m, "object": "model"} for m in (_cache["groq_text"] + _cache["or_text"])]}).encode())
        else:
            healthy = _health["last_success"] is not None and time.time() - _health["last_success"] < 3600 and _health["last_error"] is None
            self._s(200 if healthy else 503, json.dumps({"status": "ok" if healthy else "degraded", **_health}).encode())
    def do_POST(self):
        deadline = time.monotonic() + REQUEST_BUDGET
        self.connection.settimeout(10)
        if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions", "/v1/responses", "/responses"):
            self._s(404, b'{"error":{"message":"Unknown endpoint"}}'); return
        try: ln = int(self.headers.get("Content-Length", 0))
        except ValueError: self._s(400, b'{"error":{"message":"Invalid length"}}'); return
        if not 0 < ln <= 2 * 1024 * 1024:
            self._s(413, b'{"error":{"message":"Request size exceeds limit"}}'); return
        raw = self.rfile.read(ln)
        try: body = json.loads(raw)
        except Exception: self._s(400, b'{"error":"bad json"}'); return
        if not isinstance(body, dict): self._s(400, b'{"error":{"message":"Expected object"}}'); return
        is_resp = self.path.rstrip("/").endswith("/responses")
        if is_resp and body.get("stream"):
            self._s(400, b'{"error":{"message":"Responses streaming is unsupported"}}'); return
        try:
            chatbody = responses_to_chat(body) if is_resp else body
            messages = chatbody.get("messages", [])
            if not isinstance(messages, list) or not messages or any(not isinstance(m, dict) for m in messages): raise ValueError()
            img = msgs_have_image(messages)
        except (ValueError, TypeError, AttributeError):
            self._s(400, b'{"error":{"message":"Invalid message input"}}'); return
        want_stream = (not is_resp) and bool(body.get("stream"))
        recover_proxy(deadline)
        cands = candidates(img, deadline)
        # Cold discovery may expose a stale subscription before costly model attempts.
        if recover_proxy(deadline): cands = candidates(img, deadline)
        code, data, used = try_all(chatbody, cands, deadline)
        if code != 200 and recover_proxy(deadline):
            cands = candidates(img, deadline)
            code, data, used = try_all(chatbody, cands, deadline)
        log("POST", self.path, "resp_api=", is_resp, "img=", img, "cands=", len(cands), "-> code=", code, "used=", used)
        if code == 200 and data:
            if is_resp: self._s(200, chat_to_responses(data, used))
            elif want_stream: self._s(200, sse(content_of(data) or ""), "text/event-stream")
            else: self._s(200, data)
        else:
            message = "Нейросеть временно недоступна. Попробуй ещё раз немного позже."
            self._s(503, json.dumps({"error": {"message": message, "type": "upstream_unavailable", "code": str(code)}}).encode())
    def log_message(self, *a): pass

if __name__ == "__main__":
    refresh()
    ThreadingHTTPServer(LISTEN, H).serve_forever()
