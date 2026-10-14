#!/usr/bin/env python3
"""
Convenience entrypoint: python run_server.py
Direct alias for python run.py to prevent 'file not found' crashes.
"""
import sys
import run

if __name__ == "__main__":
    import uvicorn
    from backend.config import HOST, PORT
    print("=" * 60)
    print("Khin Ticket System Starting (via run_server.py)...")
    print(f"Server URL: http://{HOST}:{PORT}")
    print(f"API Docs:   http://{HOST}:{PORT}/docs")
    print("=" * 60)
    uvicorn.run("backend.main:app", host=HOST, port=PORT, reload=True)
