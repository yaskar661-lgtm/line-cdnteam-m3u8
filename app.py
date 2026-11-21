from urllib.parse import urlparse
from cachetools import TTLCache
from flask import Flask, Response, redirect, request
from flask_cors import CORS
import requests

app = Flask(__name__)

# Tüm route'lar için CORS başlıklarını aktif ediyoruz
CORS(app)

# Sadece base_ip'yi 2 dakika (120 saniye) cache'liyoruz. m3u8 İÇERİĞİ ASLA CACHE'LENMEZ!
base_ip_cache = TTLCache(maxsize=1000, ttl=120)

# Sabit MAC adresimiz
MAC_ADDRESS = "00:1A:79:1F:8F:0C"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Connection": "keep-alive",
}


def resolve_base_ip(stream_id):
  if stream_id in base_ip_cache:
    return base_ip_cache[stream_id]

  probe_url = "http://me.mdmfista.com/play/live.php"
  headers = {**DEFAULT_HEADERS, "Referer": "http://me.mdmfista.com/"}
  params = {"mac": MAC_ADDRESS, "stream": stream_id, "extension": "m3u8"}

  try:
    resp = requests.get(
        probe_url, params=params, headers=headers, timeout=5, allow_redirects=True
    )
    if resp.status_code == 200:
      parsed = urlparse(resp.url)
      if parsed.scheme and parsed.netloc:
        resolved = f"{parsed.scheme}://{parsed.netloc}"
        base_ip_cache[stream_id] = resolved
        return resolved
  except requests.RequestException:
    pass

  return "http://185.227.34.211"


# 1. ROUTE: Ana .m3u8 dosyası (Base IP cache'ten alınır, ancak playlist içeriği her seferinde taze çekilir)
@app.route("/<path:stream_id>.m3u8")
def proxy_m3u8(stream_id):
  base_ip = resolve_base_ip(stream_id)

  # Çalışan kaynak sunucu
  source_url = "http://me.mdmfista.com/play/live.php"
  headers = {
      **DEFAULT_HEADERS,
      "Referer": "http://me.mdmfista.com/",
  }
  params = {
      "mac": MAC_ADDRESS,
      "stream": stream_id,
      "extension": "m3u8",
  }

  try:
    resp = requests.get(
        source_url,
        params=params,
        headers=headers,
        timeout=8,
        allow_redirects=True,
    )

    if resp.status_code == 200:
      raw_content = resp.text.strip()

      # M3U8 içeriğindeki segment yollarını kendi proxy host'umuza çeviriyoruz
      lines = raw_content.splitlines()
      rewritten_lines = []

      for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
          if line.startswith(("http://", "https://")):
            parsed_line = urlparse(line)
            segment_path = parsed_line.path + (
                f"?{parsed_line.query}" if parsed_line.query else ""
            )
          else:
            segment_path = line if line.startswith("/") else f"/{line}"

          host_url = request.host_url.rstrip("/")
          line = f"{host_url}{segment_path}"

        rewritten_lines.append(line)

      playlist_content = "\n".join(rewritten_lines)
    else:
      return (
          f"Kaynak sunucu hata kodu döndürdü: {resp.status_code} (Son URL:"
          f" {resp.url})",
          502,
      )

  except requests.RequestException as e:
    return f"Kaynak sunucuya bağlanılamadı: {str(e)}", 502

  response = Response(playlist_content, mimetype="application/vnd.apple.mpegurl")
  # Oynatıcının veya tarayıcının eski playlist'i hafızada tutmasını engelle
  response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
  response.headers["Pragma"] = "no-cache"
  response.headers["Expires"] = "0"
  response.headers["Access-Control-Allow-Origin"] = "*"
  response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
  response.headers["Access-Control-Allow-Headers"] = "*"

  return response


# 2. ROUTE: Segment (.ts / .m4s vb.) istekleri için dinamik 302 yönlendirmesi
@app.route("/<path:subname>")
def proxy_segment(subname):
  # Cache'teki kayıtlı base_ip'leri kullanarak hızlıca yönlendir
  base_ip = "http://185.227.34.211"
  if base_ip_cache:
    base_ip = next(iter(base_ip_cache.values()))

  if not subname.startswith("/"):
    subname = f"/{subname}"

  target_url = f"{base_ip}{subname}"

  if request.query_string:
    target_url += f"?{request.query_string.decode('utf-8')}"

  response = redirect(target_url, code=302)
  response.headers["Access-Control-Allow-Origin"] = "*"
  response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
  response.headers["Access-Control-Allow-Headers"] = "*"

  return response


if __name__ == "__main__":
  app.run(host="0.0.0.0", port=5000, debug=True)
