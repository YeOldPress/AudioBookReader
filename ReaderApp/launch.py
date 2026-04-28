#!/usr/bin/env python3
"""
launch.py — Start the Heaven's River audiobook reader.

Usage:  python3 launch.py
        python3 launch.py --port 8080
"""
import argparse
import os
import sys
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from threading import Timer

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()

    port = args.port
    url  = f'http://localhost:{port}/reader.html'

    # Change to the directory containing this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    # Open browser after a short delay (give server time to start)
    Timer(0.8, lambda: webbrowser.open(url)).start()

    print(f'')
    print(f'  📚  Heaven\'s River Reader')
    print(f'  ──────────────────────────')
    print(f'  Serving from : {script_dir}')
    print(f'  Open in browser: {url}')
    print(f'')
    print(f'  Press Ctrl+C to stop.')
    print(f'')

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # silence request logs

    server = HTTPServer(('localhost', port), QuietHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n  Server stopped.')

if __name__ == '__main__':
    main()
