#!/usr/bin/env python3
"""Ongaku Renshuu's local web server.

Serves the app from its folder and downloaded videos from the media folder (with seeking),
and runs video imports (karaoke and piano) one at a time in the background.
It only listens on 127.0.0.1. Import requests must carry the X-Ongaku header and come from
the app's own address, so other web pages cannot start downloads.

Usage: server.py [--port 8765] [--root APP_DIR] [--media MEDIA_DIR]
"""
import argparse, json, os, queue, re, shutil, subprocess, sys, threading, time, urllib.parse, urllib.request
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
KINDS = {'karaoke': 'karaoke.py', 'piano': 'pianovideo.py'}


class Jobs:
    def __init__(self, media):
        self.media = media
        self.lock = threading.Lock()
        self.items = []
        self.q = queue.Queue()
        self.n = 0
        threading.Thread(target=self.worker, daemon=True).start()

    def add(self, kind, url):
        with self.lock:
            for j in self.items:      # the same video already waiting or running
                if j['url'] == url and j['kind'] == kind and j['state'] in ('waiting', 'running'):
                    return j
            self.n += 1
            j = {'id': self.n, 'kind': kind, 'url': url, 'state': 'waiting', 'stage': '', 'progress': 0,
                 'message': 'Waiting to start', 'song': None, 'added': time.time()}
            self.items.append(j)
            self.items = self.items[-50:]
        self.q.put(j)
        return j

    def list(self):
        with self.lock:
            return [dict(j) for j in self.items]

    def worker(self):
        while True:
            j = self.q.get()
            j['state'] = 'running'
            j['message'] = 'Starting'
            cmd = [sys.executable, os.path.join(HERE, KINDS[j['kind']]), j['url'], '--media', self.media]
            try:
                # low priority, so singing along to the current song stays smooth while the next ones are prepared
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     preexec_fn=(lambda: os.nice(10)) if hasattr(os, 'nice') else None)
                for line in p.stdout:
                    try:
                        m = json.loads(line)
                    except ValueError:
                        continue
                    j['stage'] = m.get('stage') or j['stage']
                    if m.get('progress') is not None:
                        j['progress'] = m['progress']
                    if m.get('message'):
                        j['message'] = m['message']
                    if m.get('stage') == 'notes' and m.get('progress') == 1:
                        j['song'] = song_id(j['url'])   # playable already; lyrics may still be coming
                p.wait()
                j['state'] = 'done' if p.returncode == 0 else 'failed'
                if p.returncode == 0:
                    j['song'] = song_id(j['url'])
            except Exception as e:
                j['state'] = 'failed'
                j['message'] = str(e)


def song_id(src):
    m = re.search(r'(?:v=|youtu\.be/|shorts/|embed/)([\w-]{11})', src)
    if m:
        return m.group(1)
    base = re.sub(r'[^\w-]+', '-', os.path.splitext(os.path.basename(src))[0]).strip('-')[:40] or 'video'
    return 'f-' + base


def read_songs(media, kind):
    root = os.path.join(media, kind)
    out = []
    if not os.path.isdir(root):
        return out
    for d in sorted(os.listdir(root)):
        f = os.path.join(root, d, 'song.json')
        try:
            s = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        out.append({'id': s.get('id', d), 'title': s.get('title'), 'artist': s.get('artist'), 'duration': s.get('duration'),
                    'created': s.get('created'), 'notes': len(s.get('notes') or []), 'lyrics': len(s.get('lyrics') or []),
                    'video': f'media/{kind}/{d}/{s.get("video", "video.mp4")}', 'song': f'media/{kind}/{d}/song.json'})
    out.sort(key=lambda s: -(s.get('created') or 0))
    return out


def ollama_up(url='http://127.0.0.1:11434'):
    try:
        urllib.request.urlopen(url + '/api/tags', timeout=1).read()
        return True
    except Exception:
        return False


def make_handler(root, media, jobs, port):
    allowed_hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
    allowed_origins = {f'http://{h}' for h in allowed_hosts}

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=root, **k)

        def log_message(self, *a):
            pass

        def end_headers(self):
            if self.path.endswith(('.html', '.json')) or self.path in ('/', ''):
                self.send_header('Cache-Control', 'no-cache')
            super().end_headers()

        # ---- safety: only our own pages may call the API
        def host_ok(self):
            return self.headers.get('Host', '') in allowed_hosts

        def api_ok(self, write=False):
            if not self.host_ok():
                return False
            origin = self.headers.get('Origin')
            if origin and origin not in allowed_origins:
                return False
            if write and self.headers.get('X-Ongaku') != '1':
                return False
            return True

        def send_json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.host_ok():
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            path = urllib.parse.urlparse(self.path).path
            if path.startswith('/api/'):
                if not self.api_ok():
                    self.send_error(HTTPStatus.FORBIDDEN)
                    return
                if path == '/api/info':
                    self.send_json({'ok': True, 'media': media, 'ollama': ollama_up(),
                                    'ffmpeg': bool(shutil.which('ffmpeg')), 'kinds': list(KINDS)})
                elif path == '/api/library':
                    self.send_json({k: read_songs(media, k) for k in KINDS})
                elif path == '/api/jobs':
                    self.send_json(jobs.list())
                else:
                    self.send_error(HTTPStatus.NOT_FOUND)
                return
            if path.startswith('/media/'):
                self.serve_file(media, path[len('/media/'):])
                return
            if path.startswith('/karaoke-demos/'):      # demo audio needs byte ranges so it can be seeked
                self.serve_file(os.path.join(root, 'karaoke-demos'), path[len('/karaoke-demos/'):])
                return
            super().do_GET()

        def do_HEAD(self):
            if not self.host_ok():
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            super().do_HEAD()

        def read_body(self):
            n = int(self.headers.get('Content-Length') or 0)
            if n > 65536:
                raise ValueError('too large')
            return json.loads(self.rfile.read(n) or b'{}')

        def do_POST(self):
            path = urllib.parse.urlparse(self.path).path
            if not self.api_ok(write=True):
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            if path == '/api/import':
                try:
                    body = self.read_body()
                except ValueError:
                    self.send_json({'error': 'Bad request'}, 400)
                    return
                url = str(body.get('url') or '').strip()
                kind = body.get('kind')
                if kind not in KINDS or not re.match(r'^https?://[^\s]+$', url) or len(url) > 500:
                    self.send_json({'error': 'Give a video link that starts with http:// or https://'}, 400)
                    return
                self.send_json(jobs.add(kind, url))
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_DELETE(self):
            path = urllib.parse.urlparse(self.path).path
            if not self.api_ok(write=True):
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            m = re.match(r'^/api/media/(karaoke|piano)/([\w-]+)$', path)
            if not m:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            d = os.path.join(media, m.group(1), m.group(2))
            if os.path.isdir(d):
                shutil.rmtree(d)
            self.send_json({'ok': True})

        def serve_file(self, base, rel):
            rel = urllib.parse.unquote(rel)
            full = os.path.realpath(os.path.join(base, rel))
            if not full.startswith(os.path.realpath(base) + os.sep) or not os.path.isfile(full):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            size = os.path.getsize(full)
            ctype = self.guess_type(full)
            rng = self.headers.get('Range')
            start, end = 0, size - 1
            m = re.match(r'bytes=(\d*)-(\d*)', rng or '')
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(size - 1, int(m.group(2)))
                else:
                    start = max(0, size - int(m.group(2)))
                if start > end:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header('Content-Range', f'bytes */{size}')
                    self.end_headers()
                    return
                self.send_response(HTTPStatus.PARTIAL_CONTENT)
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            else:
                self.send_response(HTTPStatus.OK)
            self.send_header('Content-Type', ctype)
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Length', str(end - start + 1))
            if full.endswith('.json'):
                self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            with open(full, 'rb') as f:
                f.seek(start)
                left = end - start + 1
                try:
                    while left > 0:
                        chunk = f.read(min(1 << 16, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass

    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=int(os.environ.get('ONGAKU_PORT', 8765)))
    ap.add_argument('--root', default=os.path.dirname(HERE))
    ap.add_argument('--media', default=os.environ.get('ONGAKU_MEDIA') or os.path.join(
        os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share'), 'ongaku-renshuu-media'))
    a = ap.parse_args()
    for k in KINDS:
        os.makedirs(os.path.join(a.media, k), exist_ok=True)
    jobs = Jobs(a.media)
    srv = ThreadingHTTPServer(('127.0.0.1', a.port), make_handler(os.path.abspath(a.root), a.media, jobs, a.port))
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
