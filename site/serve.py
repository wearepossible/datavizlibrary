"""
Minimal local development server for the static site.

Serves the site/ directory on http://localhost:8080 so you can test the
browsing interface locally without deploying to Netlify.  No hot-reload —
just refresh the browser after making changes.

Usage:
    python site/serve.py
"""
import http.server
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))
http.server.test(HandlerClass=http.server.SimpleHTTPRequestHandler, port=8080)
