#!/usr/bin/env python3
from flask import Flask, request, jsonify
from onvif import ONVIFCamera
import threading
import time
import os

# ============================================================
# CONFIGURATION
# ============================================================

# Actual camera settings (same as proxy_server.py)
CAMERA_IP = os.environ.get('CAMERA_IP', '127.0.0.1')
CAMERA_PORT = int(os.environ.get('CAMERA_PORT', '8000'))
CAMERA_USER = os.environ.get('CAMERA_USER', 'admin')
CAMERA_PASS = os.environ.get('CAMERA_PASS', 'admin')
WSDL_DIR = os.environ.get('WSDL_DIR', '/usr/local/lib/python3.12/site-packages/wsdl')

# PTZ server settings
PTZ_SERVER_HOST = os.environ.get('PTZ_SERVER_HOST', '0.0.0.0')
PTZ_SERVER_PORT = int(os.environ.get('PTZ_SERVER_PORT', '5001'))

app = Flask(__name__)

def get_camera():
    cam = ONVIFCamera(CAMERA_IP, CAMERA_PORT, CAMERA_USER, CAMERA_PASS, WSDL_DIR)
    ptz = cam.create_ptz_service()
    media = cam.create_media_service()
    profiles = media.GetProfiles()
    return ptz, profiles[0].token

@app.route('/ptz/<direction>')
def move_ptz(direction):
    try:
        ptz, profile_token = get_camera()
        speed = float(request.args.get('speed', 0.5))
        duration = float(request.args.get('duration', 1.0))

        movements = {
            'left': {'x': -speed, 'y': 0},
            'right': {'x': speed, 'y': 0},
            'up': {'x': 0, 'y': speed},
            'down': {'x': 0, 'y': -speed},
        }

        if direction in movements:
            move = movements[direction]

            # Start movement
            ptz.ContinuousMove({
                'ProfileToken': profile_token,
                'Velocity': {
                    'PanTilt': move,
                    'Zoom': {'x': 0}
                }
            })

            # Schedule stop
            def stop_movement():
                time.sleep(duration)
                ptz.ContinuousMove({
                    'ProfileToken': profile_token,
                    'Velocity': {
                        'PanTilt': {'x': 0, 'y': 0},
                        'Zoom': {'x': 0}
                    }
                })

            threading.Thread(target=stop_movement).start()

            return jsonify({'status': 'success', 'direction': direction, 'speed': speed, 'duration': duration})
        else:
            return jsonify({'status': 'error', 'message': 'Invalid direction'})

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

if __name__ == '__main__':
    app.run(host=PTZ_SERVER_HOST, port=PTZ_SERVER_PORT)