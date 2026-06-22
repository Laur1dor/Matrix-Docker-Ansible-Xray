#!/usr/bin/env python3
"""
baibot LLM gateway — OpenAI Chat-Completions proxy with multi-provider fallback.
Order: Groq (via xray, generous free limits, multimodal llama-4-scout) -> OpenRouter free pool -> friendly msg.
- Vision-aware: image requests use only multimodal models.
- Auto-fallback on rate-limit / unavailable.
- Friendly message instead of a raw error when everything is exhausted.
Stdlib only. systemd service on 0.0.0.0:8765. baibot -> http://172.21.0.1:8765/v1 (provider: openai_compatible)
"""
import json, sys, time, subprocess, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def rd(p):
    try: return open(p).read().strip()
    except Exception: return ""
GROQ_KEY = rd("/root/.secrets/groq_key")
OR_KEY = rd("/root/.secrets/openrouter_key")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
OR_MODELS = "https://openrouter.ai/api/v1/models"
XRAY = "http://127.0.0.1:10809"   # Groq is geo-blocked from RU -> go via xray (Finland)
LISTEN = ("0.0.0.0", 8765)
FRIENDLY = ("Извини, сейчас все бесплатные нейросети перегружены (исчерпаны лимиты). "
            "Попробуй, пожалуйста, ещё раз через минуту 🙏")
RETRY = {429, 402, 403, 500, 502, 503, 520, 524}
SKIP = ["lyria", "content-safety", "whisper", "tts", "embed", "image-gen", "stable-diffusion"]

proxied = urllib.request.build_opener(urllib.request.ProxyHandler({"http": XRAY, "https": XRAY}))
direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# candidate = (url, key, opener, model)
GROQ_TEXT = [(GROQ_URL, GROQ_KEY, proxied, "llama-3.3-70b-versatile"),
             (GROQ_URL, GROQ_KEY, proxied, "meta-llama/llama-4-scout-17b-16e-instruct")]
GROQ_VIS = [(GROQ_URL, GROQ_KEY, proxied, "meta-llama/llama-4-scout-17b-16e-instruct")]

_cache = {"t": 0.0, "or_text": [], "or_vis": []}
def log(*a): print("[gw]", *a, file=sys.stderr, flush=True)

def refresh():
    if _cache["or_text"] and time.time() - _cache["t"] < 1800:
        return
    try:
        r = direct.open(urllib.request.Request(OR_MODELS, headers={"Authorization": "Bearer " + OR_KEY}), timeout=20)
        d = json.loads(r.read()); t = []; v = []
        for m in d.get("data", []):
            pr = m.get("pricing", {}); mid = m.get("id", "")
            if pr.get("prompt") != "0" or pr.get("completion") != "0": continue
            if any(s in mid for s in SKIP): continue
            t.append((OR_URL, OR_KEY, direct, mid))
            if "image" in m.get("architecture", {}).get("input_modalities", []):
                v.append((OR_URL, OR_KEY, direct, mid))
        if t:
            _cache.update(t=time.time(), or_text=t, or_vis=v)
    except Exception:
        pass

def candidates(vision):
    refresh()
    return (GROQ_VIS + _cache["or_vis"]) if vision else (GROQ_TEXT + _cache["or_text"])

def has_image(body):
    for msg in body.get("messages", []):
        c = msg.get("content")
        if isinstance(c, list):
            for p in c:
                if isinstance(p, dict) and p.get("type") in ("image_url", "input_image"):
                    return True
    return False

def try_all(body, cands):
    # Uses curl (reliable HTTPS-over-proxy tunneling, unlike urllib) per upstream call.
    payload = dict(body); payload["stream"] = False
    last = (503, None)
    for (url, key, opener, model) in cands:
        payload["model"] = model
        cmd = ["curl", "-s", "-m", "45", "-w", "\n%{http_code}", "-X", "POST", url,
               "-H", "Authorization: Bearer " + key, "-H", "Content-Type: application/json",
               "-H", "HTTP-Referer: https://void-shell.com", "-H", "X-Title: void-shell",
               "--data-binary", "@-"]
        if opener is proxied:
            cmd[1:1] = ["-x", XRAY]   # tunnel Groq through xray (-> Finland)
        try:
            p = subprocess.run(cmd, input=json.dumps(payload).encode(), capture_output=True, timeout=60)
            out = p.stdout
            nl = out.rfind(b"\n")
            raw = out[:nl] if nl >= 0 else out
            code = int(out[nl+1:].strip() or 0) if nl >= 0 else 0
            if code == 200 and raw:
                j = json.loads(raw)
                if isinstance(j, dict) and j.get("error"):
                    last = (429, raw); continue
                return 200, raw, model
            last = (code, raw)
            if code in RETRY or code == 0:
                continue
            return code, raw, model   # genuine error -> surface
        except Exception:
            last = (503, None); continue
    return last[0], last[1], None

def sse(text):
    ch = {"id": "gw", "object": "chat.completion.chunk", "created": int(time.time()), "model": "gateway",
          "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}
    return ("data: " + json.dumps(ch) + "\n\ndata: [DONE]\n\n").encode()

def comp(text):
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
            ms = [c[3] for c in (GROQ_TEXT + _cache["or_text"])]
            self._s(200, json.dumps({"object": "list", "data": [{"id": m, "object": "model"} for m in ms]}).encode())
        else:
            self._s(200, b'{"status":"ok"}')
    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0)); raw = self.rfile.read(ln)
        try: body = json.loads(raw)
        except Exception: self._s(400, b'{"error":"bad json"}'); return
        want_stream = bool(body.get("stream"))
        img = has_image(body)
        cands = candidates(img)
        code, data, used = try_all(body, cands)
        log("POST", self.path, "nmsg=", len(body.get("messages", [])), "img=", img,
            "cands=", len(cands), "-> code=", code, "used=", used)
        if code == 200 and data:
            if want_stream:
                try: content = json.loads(data)["choices"][0]["message"].get("content", "")
                except Exception: content = ""
                self._s(200, sse(content), "text/event-stream")
            else:
                self._s(200, data)
        elif code and code not in RETRY and data:
            self._s(code, data)
        else:
            self._s(200, sse(FRIENDLY) if want_stream else comp(FRIENDLY),
                    "text/event-stream" if want_stream else "application/json")
    def log_message(self, *a): pass

if __name__ == "__main__":
    refresh()
    ThreadingHTTPServer(LISTEN, H).serve_forever()
