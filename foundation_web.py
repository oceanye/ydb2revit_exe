# coding: utf-8
"""Local, dependency-free reinforcement editor for foundation handoff data."""

from __future__ import annotations

import argparse
import json
import secrets
import sqlite3
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from foundation_handoff import (
    FoundationDataError,
    read_editor_data,
    update_cap_rebar,
    update_pile_rebar,
)


HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>YDB 基础配筋补录</title>
<style>
:root { color-scheme: light; --ink:#17212b; --muted:#647180; --line:#d8dee6;
  --paper:#fff; --bg:#f3f5f7; --brand:#176b5b; --brand2:#e6f2ef; --danger:#a12a2a; }
* { box-sizing:border-box; }
body { margin:0; font-family:"Microsoft YaHei UI","Segoe UI",sans-serif;
  color:var(--ink); background:var(--bg); }
header { background:#102c29; color:white; padding:24px max(24px,calc((100% - 1240px)/2)); }
header h1 { margin:0 0 7px; font-size:24px; }
header p { margin:0; color:#c8ded9; font-size:14px; }
main { max-width:1240px; margin:22px auto 60px; padding:0 20px; }
.notice { background:#fff8df; border:1px solid #eadb9c; border-radius:8px;
  padding:12px 15px; margin-bottom:18px; font-size:14px; }
.summary { display:grid; grid-template-columns:repeat(4,minmax(120px,1fr)); gap:12px; margin-bottom:24px; }
.metric { background:var(--paper); border:1px solid var(--line); border-radius:9px; padding:14px; }
.metric b { display:block; font-size:25px; color:var(--brand); }
.metric span { color:var(--muted); font-size:13px; }
h2 { margin:28px 0 12px; font-size:20px; }
.type-card { background:var(--paper); border:1px solid var(--line); border-radius:10px;
  margin-bottom:14px; overflow:hidden; }
.type-head { padding:13px 16px; background:#f9fafb; border-bottom:1px solid var(--line);
  display:flex; align-items:center; justify-content:space-between; gap:14px; }
.type-head strong { font-size:16px; }
.key { color:var(--muted); font-family:Consolas,monospace; font-size:12px; }
.dims { font-size:13px; color:var(--muted); text-align:right; }
.form { display:grid; grid-template-columns:repeat(4,minmax(130px,1fr)); gap:12px;
  padding:15px 16px 16px; }
label { display:block; font-size:12px; color:var(--muted); }
input,textarea { width:100%; margin-top:5px; border:1px solid #bfc8d2; border-radius:6px;
  padding:8px 9px; font:14px inherit; color:var(--ink); background:white; }
textarea { min-height:72px; resize:vertical; }
.span2 { grid-column:span 2; }
.span4 { grid-column:span 4; }
.actions { display:flex; justify-content:flex-end; align-items:center; gap:10px; grid-column:span 4; }
button { border:0; border-radius:6px; padding:9px 18px; background:var(--brand); color:white;
  font-weight:600; cursor:pointer; }
button:disabled { opacity:.55; cursor:wait; }
.status { font-size:13px; color:var(--brand); min-height:18px; }
.error { color:var(--danger); }
.empty { padding:28px; text-align:center; color:var(--muted); background:white;
  border:1px dashed var(--line); border-radius:8px; }
@media (max-width:800px) {
  .summary { grid-template-columns:repeat(2,1fr); }
  .form { grid-template-columns:repeat(2,1fr); }
  .span4,.actions { grid-column:span 2; }
}
</style>
</head>
<body>
<header><h1>基础配筋补录</h1><p>几何与定位只读自 YDB；本页只补录统一中间数据库 tbl5、tbl6 的配筋字段。</p></header>
<main>
  <div class="notice"><b>数据边界：</b>不读取 DWG，不从 YDB 提取配筋；支持单阶矩形/多边形承台和竖直桩。保存结果直接写入 Revit 交接数据库。</div>
  <div id="summary" class="summary"></div>
  <h2>承台类型</h2><div id="caps"></div>
  <h2>桩类型</h2><div id="piles"></div>
</main>
<script>
const TOKEN = __TOKEN__;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const val = value => value == null ? '' : value;
const field = (label,name,value,cls='') => `<label class="${cls}">${label}<input name="${name}" value="${esc(val(value))}"></label>`;
const area = (label,name,value,cls='') => `<label class="${cls}">${label}<textarea name="${name}">${esc(val(value))}</textarea></label>`;

function capDimensions(t) {
  return `${t.VertexCount} 边形；厚 ${t.Thickness} mm；${t.PileCount} 桩/承台；${t.InstanceCount} 个布置`;
}

function pileDimensions(t) {
  return `D=${t.Diameter} mm；L=${t.Length} mm；${t.InstanceCount} 根`;
}

function capCard(t) {
  return `<section class="type-card" data-kind="cap" data-key="${esc(t.TypeKey)}">
    <div class="type-head"><div><strong>${esc(t.UserTypeName || `承台类型 ${t.ID}`)}</strong><div class="key">${esc(t.TypeKey)}</div></div><div class="dims">${esc(capDimensions(t))}</div></div>
    <div class="form">
      ${field('用户类型名称','UserTypeName',t.UserTypeName)}
      ${field('保护层 mm','Cover',t.Cover)}
      ${field('底筋 X 向','BottomX',t.BottomX)}
      ${field('底筋 Y 向','BottomY',t.BottomY)}
      ${field('顶筋 X 向','TopX',t.TopX)}
      ${field('顶筋 Y 向','TopY',t.TopY)}
      ${field('侧面钢筋','SideRebar',t.SideRebar,'span2')}
      ${area('备注','Notes',t.Notes,'span2')}
      ${area('扩展参数 JSON','ExtraJson',t.ExtraJson || '{}','span2')}
      <div class="actions"><span class="status"></span><button onclick="saveCard(this)">保存承台配筋</button></div>
    </div></section>`;
}

function pileCard(t) {
  return `<section class="type-card" data-kind="pile" data-key="${esc(t.TypeKey)}">
    <div class="type-head"><div><strong>${esc(t.UserTypeName || `桩类型 ${t.ID}`)}</strong><div class="key">${esc(t.TypeKey)}</div></div><div class="dims">${esc(pileDimensions(t))}</div></div>
    <div class="form">
      ${field('用户类型名称','UserTypeName',t.UserTypeName)}
      ${field('保护层 mm','Cover',t.Cover)}
      ${field('纵筋','LongitudinalRebar',t.LongitudinalRebar)}
      ${field('一般段箍筋/螺旋筋','StirrupRebar',t.StirrupRebar)}
      ${field('加密段箍筋/螺旋筋','DenseStirrupRebar',t.DenseStirrupRebar,'span2')}
      ${field('加密区长度 mm','DenseZoneLength',t.DenseZoneLength,'span2')}
      ${area('备注','Notes',t.Notes,'span2')}
      ${area('扩展参数 JSON','ExtraJson',t.ExtraJson || '{}','span2')}
      <div class="actions"><span class="status"></span><button onclick="saveCard(this)">保存桩配筋</button></div>
    </div></section>`;
}

async function saveCard(button) {
  const card = button.closest('.type-card');
  const status = card.querySelector('.status');
  const payload = {};
  card.querySelectorAll('input,textarea').forEach(el => payload[el.name] = el.value);
  button.disabled = true; status.className='status'; status.textContent='保存中…';
  try {
    const response = await fetch(`/api/${card.dataset.kind}/${encodeURIComponent(card.dataset.key)}`, {
      method:'PUT', headers:{'Content-Type':'application/json','X-Foundation-Token':TOKEN}, body:JSON.stringify(payload)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '保存失败');
    status.textContent='已保存';
    const title = card.querySelector('strong');
    if (payload.UserTypeName) title.textContent=payload.UserTypeName;
  } catch (error) { status.className='status error'; status.textContent=error.message; }
  finally { button.disabled=false; }
}

async function load() {
  try {
    const response = await fetch('/api/data', {headers:{'X-Foundation-Token':TOKEN}});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '读取失败');
    const labels = [['承台类型',data.summary.cap_types],['承台实例',data.summary.caps],['桩类型',data.summary.pile_types],['桩实例',data.summary.piles]];
    document.getElementById('summary').innerHTML=labels.map(x=>`<div class="metric"><b>${x[1]}</b><span>${x[0]}</span></div>`).join('');
    document.getElementById('caps').innerHTML=data.cap_types.length ? data.cap_types.map(capCard).join('') : '<div class="empty">没有承台类型</div>';
    document.getElementById('piles').innerHTML=data.pile_types.length ? data.pile_types.map(pileCard).join('') : '<div class="empty">没有桩类型</div>';
  } catch (error) {
    document.querySelector('main').innerHTML=`<div class="notice error">${esc(error.message)}</div>`;
  }
}
load();
</script>
</body></html>"""


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _handler_factory(database_path, token):
    database_path = str(Path(database_path).expanduser().resolve())
    html = HTML_TEMPLATE.replace("__TOKEN__", json.dumps(token)).encode("utf-8")

    class FoundationRequestHandler(BaseHTTPRequestHandler):
        server_version = "FoundationEditor/1"

        def _headers(self, status, content_type, length):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'")
            self.end_headers()

        def _send_json(self, status, value):
            body = _json_bytes(value)
            self._headers(status, "application/json; charset=utf-8", len(body))
            self.wfile.write(body)

        def _authorised(self):
            return secrets.compare_digest(
                self.headers.get("X-Foundation-Token", ""), token
            )

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                self._headers(200, "text/html; charset=utf-8", len(html))
                self.wfile.write(html)
                return
            if path == "/api/data":
                if not self._authorised():
                    self._send_json(403, {"error": "forbidden"})
                    return
                try:
                    self._send_json(200, read_editor_data(database_path))
                except (FoundationDataError, sqlite3.Error, OSError) as error:
                    self._send_json(400, {"error": str(error)})
                return
            self._send_json(404, {"error": "not found"})

        def do_PUT(self):
            path = urlparse(self.path).path
            if not self._authorised():
                self._send_json(403, {"error": "forbidden"})
                return
            parts = path.strip("/").split("/", 2)
            if len(parts) != 3 or parts[0] != "api" or parts[1] not in ("cap", "pile"):
                self._send_json(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 1024 * 1024:
                    raise FoundationDataError("invalid request size")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise FoundationDataError("request body must be a JSON object")
                type_key = unquote(parts[2])
                if parts[1] == "cap":
                    result = update_cap_rebar(database_path, type_key, payload)
                else:
                    result = update_pile_rebar(database_path, type_key, payload)
                self._send_json(200, {"ok": True, "values": result})
            except (FoundationDataError, json.JSONDecodeError, UnicodeDecodeError, sqlite3.Error, OSError) as error:
                self._send_json(400, {"error": str(error)})

        def log_message(self, format_string, *args):
            print("foundation-web:", format_string % args)

    return FoundationRequestHandler


def serve_foundation_editor(database_path, host="127.0.0.1", port=8765, open_browser=True):
    """Run the local editor until interrupted and return its bound URL."""
    database_path = Path(database_path).expanduser().resolve()
    read_editor_data(database_path)
    token = secrets.token_urlsafe(24)
    server = ThreadingHTTPServer((host, int(port)), _handler_factory(database_path, token))
    actual_host, actual_port = server.server_address[:2]
    browser_host = "127.0.0.1" if actual_host in ("0.0.0.0", "::") else actual_host
    url = "http://{}:{}/".format(browser_host, actual_port)
    print("Foundation editor:", url)
    print("Database:", database_path)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return url


def main(argv=None):
    parser = argparse.ArgumentParser(description="Edit foundation reinforcement in a local web page")
    parser.add_argument("database", help="Revit handoff SQLite database")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    serve_foundation_editor(
        args.database,
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
