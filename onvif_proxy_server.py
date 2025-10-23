#!/usr/bin/env python3
"""
ONVIF PTZ Proxy Server with MoveStatus support.

This server acts as an ONVIF proxy that adds MoveStatus and GetStatus
tracking for cameras that don't natively support it. Connect your ONVIF
clients to this proxy instead of directly to the camera.

Usage:
    python3 onvif_proxy_server.py

Then connect to: localhost:8000 (or proxy_host:8000)
"""

from flask import Flask, request, Response
from onvif_ptz_wrapper import ONVIFPTZWrapper
from onvif import ONVIFCamera
import zeep
from zeep import Client
from zeep.transports import Transport
from lxml import etree
import logging
import requests
from requests.auth import HTTPDigestAuth
import re

# Configuration
CAMERA_IP = '192.168.178.176'
CAMERA_PORT = 8000
CAMERA_USER = 'admin'
CAMERA_PASS = 'admin'
WSDL_DIR = './.venv/lib/python3.10/site-packages/wsdl/'

PROXY_HOST = '0.0.0.0'
PROXY_PORT = 8000

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Global camera and wrapper instances
camera = None
ptz_wrapper = None
services = {}


def init_camera():
    """Initialize camera connection and wrapper."""
    global camera, ptz_wrapper, services

    logger.info(f"Connecting to camera at {CAMERA_IP}:{CAMERA_PORT}")
    camera = ONVIFCamera(CAMERA_IP, CAMERA_PORT, CAMERA_USER, CAMERA_PASS, WSDL_DIR)

    # Create services
    services['device'] = camera.devicemgmt
    services['media'] = camera.create_media_service()
    services['ptz'] = camera.create_ptz_service()

    # Create PTZ wrapper
    ptz_wrapper = ONVIFPTZWrapper(camera)

    logger.info("Camera connected and wrapper initialized")


def proxy_soap_request(service_name, soap_body):
    """
    Proxy a SOAP request to the actual camera.

    Args:
        service_name: Name of the ONVIF service (device, media, ptz, etc.)
        soap_body: Raw SOAP request body

    Returns:
        SOAP response from camera
    """
    # Determine the target URL based on service
    if service_name == 'device':
        target_url = services['device'].xaddr
    elif service_name == 'media':
        target_url = services['media'].xaddr
    elif service_name == 'ptz':
        target_url = services['ptz'].xaddr
    else:
        # Default to device management
        target_url = f"http://{CAMERA_IP}:{CAMERA_PORT}/onvif/{service_name}_service"

    # Replace localhost references with actual camera IP in the request
    soap_body = soap_body.replace('localhost', CAMERA_IP)
    soap_body = soap_body.replace(f':{PROXY_PORT}/', f':{CAMERA_PORT}/')

    # Forward the SOAP request to the camera
    headers = {
        'Content-Type': 'application/soap+xml; charset=utf-8',
    }

    try:
        response = requests.post(
            target_url,
            data=soap_body,
            headers=headers,
            auth=HTTPDigestAuth(CAMERA_USER, CAMERA_PASS),
            timeout=10
        )

        # Replace camera IP with localhost in response
        response_text = response.text.replace(CAMERA_IP, 'localhost')
        response_text = response_text.replace(f':{CAMERA_PORT}/', f':{PROXY_PORT}/')

        return response_text, response.status_code

    except Exception as e:
        logger.error(f"Error proxying request: {e}")
        return f"<soap:Envelope><soap:Body><soap:Fault><faultstring>{str(e)}</faultstring></soap:Fault></soap:Body></soap:Envelope>", 500


def intercept_ptz_command(operation, soap_body):
    """
    Intercept PTZ commands to track status.

    Args:
        operation: PTZ operation name (ContinuousMove, RelativeMove, etc.)
        soap_body: SOAP request body

    Returns:
        Tuple of (response_text, status_code, intercepted)
    """
    try:
        # Parse the SOAP request
        root = etree.fromstring(soap_body.encode())

        # Define namespaces
        namespaces = {
            'soap': 'http://www.w3.org/2003/05/soap-envelope',
            'tptz': 'http://www.onvif.org/ver20/ptz/wsdl',
            'tt': 'http://www.onvif.org/ver10/schema'
        }

        if operation == 'GetStatus':
            # Intercept GetStatus and return our tracked status
            logger.info("Intercepting GetStatus request")

            # Get profile token from request
            profile_token_elem = root.find('.//tptz:ProfileToken', namespaces)
            if profile_token_elem is None:
                profile_token_elem = root.find('.//{*}ProfileToken')

            profile_token = profile_token_elem.text if profile_token_elem is not None else None

            # Get status from wrapper
            status = ptz_wrapper.GetStatus()

            # Build SOAP response with MoveStatus
            response = f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"
                   xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"
                   xmlns:tt="http://www.onvif.org/ver10/schema">
    <SOAP-ENV:Body>
        <tptz:GetStatusResponse>
            <tptz:PTZStatus>
                <tt:Position>
                    <tt:PanTilt x="{status.Position.PanTilt['x']}" y="{status.Position.PanTilt['y']}"
                                space="http://www.onvif.org/ver10/tptz/PanTiltSpaces/PositionGenericSpace"/>
                    <tt:Zoom x="{status.Position.Zoom['x']}"
                            space="http://www.onvif.org/ver10/tptz/ZoomSpaces/PositionGenericSpace"/>
                </tt:Position>
                <tt:MoveStatus>
                    <tt:PanTilt>{status.MoveStatus.PanTilt}</tt:PanTilt>
                    <tt:Zoom>{status.MoveStatus.Zoom}</tt:Zoom>
                </tt:MoveStatus>
                <tt:UTCTime>{status.UTCTime.isoformat()}Z</tt:UTCTime>
            </tptz:PTZStatus>
        </tptz:GetStatusResponse>
    </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""

            return response, 200, True

        elif operation in ['ContinuousMove', 'RelativeMove', 'AbsoluteMove', 'Stop']:
            # Parse the request and call wrapper method
            logger.info(f"Intercepting {operation} request")

            # Extract parameters from SOAP
            profile_token_elem = root.find('.//tptz:ProfileToken', namespaces)
            if profile_token_elem is None:
                profile_token_elem = root.find('.//{*}ProfileToken')

            profile_token = profile_token_elem.text if profile_token_elem is not None else ptz_wrapper.profile_token

            request_dict = {'ProfileToken': profile_token}

            if operation == 'ContinuousMove':
                # Extract Velocity
                velocity = {}

                pan_tilt_elem = root.find('.//tt:PanTilt', namespaces)
                if pan_tilt_elem is not None:
                    velocity['PanTilt'] = {
                        'x': float(pan_tilt_elem.get('x', 0)),
                        'y': float(pan_tilt_elem.get('y', 0))
                    }

                zoom_elem = root.find('.//tt:Zoom', namespaces)
                if zoom_elem is not None:
                    velocity['Zoom'] = {'x': float(zoom_elem.get('x', 0))}

                request_dict['Velocity'] = velocity

                # Extract Timeout if present
                timeout_elem = root.find('.//tptz:Timeout', namespaces)
                if timeout_elem is not None:
                    # Parse ISO 8601 duration (PT2S = 2 seconds)
                    timeout_text = timeout_elem.text
                    match = re.search(r'PT(\d+(?:\.\d+)?)S', timeout_text)
                    if match:
                        request_dict['Timeout'] = float(match.group(1))

                # Call wrapper
                ptz_wrapper.ContinuousMove(request_dict)

            elif operation == 'RelativeMove':
                # Extract Translation
                translation = {}

                pan_tilt_elem = root.find('.//tt:PanTilt', namespaces)
                if pan_tilt_elem is not None:
                    translation['PanTilt'] = {
                        'x': float(pan_tilt_elem.get('x', 0)),
                        'y': float(pan_tilt_elem.get('y', 0))
                    }

                zoom_elem = root.find('.//tt:Zoom', namespaces)
                if zoom_elem is not None:
                    translation['Zoom'] = {'x': float(zoom_elem.get('x', 0))}

                request_dict['Translation'] = translation

                # Call wrapper
                ptz_wrapper.RelativeMove(request_dict)

            elif operation == 'AbsoluteMove':
                # Extract Position
                position = {}

                pan_tilt_elem = root.find('.//tt:PanTilt', namespaces)
                if pan_tilt_elem is not None:
                    position['PanTilt'] = {
                        'x': float(pan_tilt_elem.get('x', 0)),
                        'y': float(pan_tilt_elem.get('y', 0))
                    }

                zoom_elem = root.find('.//tt:Zoom', namespaces)
                if zoom_elem is not None:
                    position['Zoom'] = {'x': float(zoom_elem.get('x', 0))}

                request_dict['Position'] = position

                # Call wrapper
                ptz_wrapper.AbsoluteMove(request_dict)

            elif operation == 'Stop':
                # Extract stop flags
                pan_tilt_elem = root.find('.//tptz:PanTilt', namespaces)
                zoom_elem = root.find('.//tptz:Zoom', namespaces)

                if pan_tilt_elem is not None:
                    request_dict['PanTilt'] = pan_tilt_elem.text.lower() == 'true'
                if zoom_elem is not None:
                    request_dict['Zoom'] = zoom_elem.text.lower() == 'true'

                # Call wrapper
                ptz_wrapper.Stop(request_dict)

            # Build simple success response
            response = f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"
                   xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">
    <SOAP-ENV:Body>
        <tptz:{operation}Response/>
    </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""

            return response, 200, True

    except Exception as e:
        logger.error(f"Error intercepting {operation}: {e}", exc_info=True)
        return None, None, False

    return None, None, False


@app.route('/onvif/<service>', methods=['POST'])
def handle_onvif_service(service):
    """Handle ONVIF SOAP requests."""
    soap_body = request.data.decode('utf-8')

    # Detect the operation from SOAP body
    operation_match = re.search(r'<.*?:(\w+)(?:\s|>)', soap_body)
    operation = operation_match.group(1) if operation_match else None

    logger.info(f"Received {operation} request for {service} service")

    # Intercept PTZ commands if this is PTZ service
    if service == 'ptz_service' and operation:
        intercepted_response, status_code, intercepted = intercept_ptz_command(operation, soap_body)
        if intercepted:
            return Response(intercepted_response, status=status_code, mimetype='application/soap+xml')

    # Proxy to actual camera
    response_text, status_code = proxy_soap_request(service.replace('_service', ''), soap_body)

    return Response(response_text, status=status_code, mimetype='application/soap+xml')


@app.route('/')
def index():
    """Index page with proxy information."""
    return f"""
    <html>
    <head><title>ONVIF PTZ Proxy</title></head>
    <body>
        <h1>ONVIF PTZ Proxy Server</h1>
        <p>This proxy adds MoveStatus support to your ONVIF camera.</p>
        <ul>
            <li><strong>Proxy Address:</strong> {request.host}</li>
            <li><strong>Target Camera:</strong> {CAMERA_IP}:{CAMERA_PORT}</li>
            <li><strong>Username:</strong> {CAMERA_USER}</li>
        </ul>
        <h2>Features</h2>
        <ul>
            <li>GetStatus with MoveStatus tracking</li>
            <li>ContinuousMove tracking</li>
            <li>RelativeMove tracking</li>
            <li>AbsoluteMove tracking</li>
            <li>Stop command tracking</li>
        </ul>
        <h2>Usage</h2>
        <pre>
from onvif import ONVIFCamera

camera = ONVIFCamera('localhost', {PROXY_PORT}, '{CAMERA_USER}', '{CAMERA_PASS}', wsdl_dir)
ptz = camera.create_ptz_service()

# Now GetStatus works with MoveStatus!
status = ptz.GetStatus({{'ProfileToken': 'PROFILE_000'}})
print(status.MoveStatus.PanTilt)  # IDLE or MOVING
        </pre>
    </body>
    </html>
    """


if __name__ == '__main__':
    try:
        init_camera()
        logger.info(f"Starting ONVIF proxy server on {PROXY_HOST}:{PROXY_PORT}")
        logger.info(f"Proxying to camera at {CAMERA_IP}:{CAMERA_PORT}")
        logger.info(f"Connect your ONVIF client to: localhost:{PROXY_PORT}")
        app.run(host=PROXY_HOST, port=PROXY_PORT, debug=False)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        if ptz_wrapper:
            ptz_wrapper.cleanup()
    except Exception as e:
        logger.error(f"Failed to start proxy: {e}", exc_info=True)
