"""Phone remote: a small web page on your home network that shows the Flipper's screen and has a d-pad.

Safety: off until switched on; every request needs the random code in the link (compared in constant
time); only private-network addresses (10.x, 172.16-31.x, 192.168.x, localhost) are answered; it
stops when switched off or when Workbench closes. It can only show the screen and press buttons.
"""

import base64
import ipaddress
import json
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>Flipper remote</title><style>
body{margin:0;background:#0d0d0d;color:#eee;font:15px system-ui,sans-serif;display:flex;flex-direction:column;align-items:center;gap:18px;padding:18px;user-select:none;-webkit-user-select:none}
h1{font-size:15px;color:#ff8200;margin:0;letter-spacing:1px}
#lcd{width:min(92vw,512px);aspect-ratio:2/1;image-rendering:pixelated;border-radius:8px;border:8px solid #050505;background:#ff8c29}
.pad{position:relative;width:230px;height:230px}
.pad button,.back{position:absolute;border:0;background:#262626;color:#ccc;font-size:26px;border-radius:14px;touch-action:none}
.pad button:active,.back:active,.pad button.on{background:#ff8200;color:#000}
.up{left:80px;top:0;width:70px;height:70px}.down{left:80px;bottom:0;width:70px;height:70px}
.left{left:0;top:80px;width:70px;height:70px}.right{right:0;top:80px;width:70px;height:70px}
.ok{left:80px;top:80px;width:70px;height:70px;border-radius:50%!important}
.row{display:flex;gap:20px;align-items:center}.back{position:static;width:80px;height:56px}
#st{color:#888;font-size:13px}
</style></head><body><h1>FLIPPER WORKBENCH REMOTE</h1><canvas id="lcd" width="128" height="64"></canvas>
<div class="row"><div class="pad"><button class="up" data-k="up">&#9650;</button><button class="left" data-k="left">&#9664;</button>
<button class="ok" data-k="ok">OK</button><button class="right" data-k="right">&#9654;</button><button class="down" data-k="down">&#9660;</button></div>
<button class="back" data-k="back">&#8617;</button></div><div id="st">Connecting...</div>
<script>
const T=new URLSearchParams(location.search).get("t"),c=document.getElementById("lcd").getContext("2d"),img=c.createImageData(128,64);let seq=-1;
async function poll(){try{const r=await fetch(`/frame?t=${T}&since=${seq}`);if(r.status==403){st.textContent="This link expired. Get a new one in Workbench.";return}
const j=await r.json();if(j.seq!==seq&&j.frame){seq=j.seq;const d=Uint8Array.from(atob(j.frame),ch=>ch.charCodeAt(0)),flip=j.orient==1;
for(let y=0;y<64;y++)for(let x=0;x<128;x++){const on=(d[(y>>3)*128+x]>>(y&7))&1,sx=flip?127-x:x,sy=flip?63-y:y,k=(sy*128+sx)*4;img.data[k]=img.data[k+1]=img.data[k+2]=0;img.data[k+3]=on?255:0}
c.putImageData(img,0,0)}st.textContent=j.live?"Live":"Waiting for the mirror in Workbench..."}catch(e){st.textContent="Lost the connection, retrying..."}setTimeout(poll,90)}
function send(k,kind){fetch(`/press?t=${T}&key=${k}&kind=${kind}`,{method:"POST"})}
document.querySelectorAll("[data-k]").forEach(b=>{let t1,long=false;
b.addEventListener("pointerdown",e=>{e.preventDefault();long=false;b.classList.add("on");send(b.dataset.k,"press");t1=setTimeout(()=>{long=true;send(b.dataset.k,"long")},500)});
const up=()=>{if(!b.classList.contains("on"))return;b.classList.remove("on");clearTimeout(t1);if(!long)send(b.dataset.k,"short");send(b.dataset.k,"release")};
b.addEventListener("pointerup",up);b.addEventListener("pointerleave",up);b.addEventListener("pointercancel",up)});
poll();
</script></body></html>"""

KEYS = {"up", "down", "left", "right", "ok", "back"}
KINDS = {"press", "release", "short", "long", "repeat"}


def lan_ip():
    """This PC's address on the home network (no traffic is sent; UDP connect just picks a route)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _allowed(addr):
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


class Remote:
    def __init__(self, get_frame, press, ensure_mirror, port=8780):
        self.get_frame, self.press, self.ensure_mirror = get_frame, press, ensure_mirror
        self.token = secrets.token_urlsafe(9)
        self.clients = set()
        self.server = None
        self.port = port

    def url(self):
        return f"http://{lan_ip()}:{self.port}/?t={self.token}"

    def start(self):
        remote = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):   # quiet
                pass

            def _ok_client(self):
                if not _allowed(self.client_address[0]):
                    self.send_error(403)
                    return None
                q = parse_qs(urlparse(self.path).query)
                if not secrets.compare_digest((q.get("t") or [""])[0], remote.token):
                    self.send_error(403)
                    return None
                remote.clients.add(self.client_address[0])
                return q

            def _send(self, body, ctype):
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                q = self._ok_client()
                if q is None:
                    return
                path = urlparse(self.path).path
                if path == "/":
                    remote.ensure_mirror()
                    self._send(PAGE.encode(), "text/html; charset=utf-8")
                elif path == "/frame":
                    seq, frame, orient, live = remote.get_frame()
                    out = {"seq": seq, "live": live, "orient": orient,
                           "frame": base64.b64encode(frame).decode() if frame is not None and str(seq) != (q.get("since") or [""])[0] else None}
                    self._send(json.dumps(out).encode(), "application/json")
                else:
                    self.send_error(404)

            def do_POST(self):
                q = self._ok_client()
                if q is None:
                    return
                if urlparse(self.path).path != "/press":
                    self.send_error(404)
                    return
                key, kind = (q.get("key") or [""])[0], (q.get("kind") or [""])[0]
                if key in KEYS and kind in KINDS:
                    remote.press(key, kind)
                self._send(b"{}", "application/json")

        for port in range(self.port, self.port + 20):
            try:
                self.server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
                self.port = port
                break
            except OSError:
                continue
        if not self.server:
            raise OSError("No free port for the phone remote.")
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self.url()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
