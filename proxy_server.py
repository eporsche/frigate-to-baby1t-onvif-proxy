#!/usr/bin/env python3
"""
ONVIF Proxy Server with MoveStatus support.

Main server that ties together all components:
- SOAP message handling
- Service proxying to actual camera
- PTZ command interception for MoveStatus tracking

This allows Frigate and other ONVIF clients to connect to the proxy
and get MoveStatus support for cameras that don't natively support it.

Usage:
    1. Configure camera settings in config section below
    2. Run: python3 proxy_server.py
    3. Connect Frigate to localhost:8000 (or proxy_host:8000)
"""

from flask import Flask, request, Response
from onvif import ONVIFCamera
from onvif_ptz_wrapper import ONVIFPTZWrapper
from onvif_proxy import ONVIFServiceProxy
from ptz_interceptor import PTZInterceptor
from soap_handler import parse_soap_request
from lxml import etree
import logging
import sys
import os

# ============================================================
# CONFIGURATION
# ============================================================

# Actual camera settings
CAMERA_IP = os.environ.get('CAMERA_IP', '127.0.0.1')
CAMERA_PORT = int(os.environ.get('CAMERA_PORT', '8000'))
CAMERA_USER = os.environ.get('CAMERA_USER', 'admin')
CAMERA_PASS = os.environ.get('CAMERA_PASS', 'admin')
WSDL_DIR = os.environ.get('WSDL_DIR', '/usr/local/lib/python3.12/site-packages/wsdl')

# Proxy server settings
PROXY_HOST = os.environ.get('PROXY_HOST', '0.0.0.0')  # Listen on all interfaces
PROXY_PORT = int(os.environ.get('PROXY_PORT', '8000'))
PROXY_EXTERNAL_HOST = os.environ.get('PROXY_EXTERNAL_HOST', '127.0.0.1')  # External hostname (for SOAP address rewriting)

# Logging
LOG_LEVEL = getattr(logging, os.environ.get('LOG_LEVEL', 'INFO').upper(), logging.INFO)

# ============================================================
# SETUP
# ============================================================

# Configure logging
logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Flask app
app = Flask(__name__)

# Global components (initialized on startup)
camera = None
ptz_wrapper = None
service_proxy = None
ptz_interceptor = None


def add_fov_to_config_options(soap_response: str) -> str:
    """
    Add FOV-based RelativePanTiltTranslationSpace to GetConfigurationOptions response.

    This makes Frigate think the camera supports FOV-based relative moves,
    which the proxy will translate to ContinuousMove.

    Args:
        soap_response: Original SOAP response from camera

    Returns:
        Modified SOAP response with FOV space added
    """
    try:
        # Parse the SOAP response
        root = etree.fromstring(soap_response.encode())

        # Find RelativePanTiltTranslationSpace elements
        namespaces = {
            'soap': 'http://www.w3.org/2003/05/soap-envelope',
            'tptz': 'http://www.onvif.org/ver20/ptz/wsdl',
            'tt': 'http://www.onvif.org/ver10/schema',
        }

        # Find the Spaces element
        spaces = root.find('.//{http://www.onvif.org/ver10/schema}Spaces')
        if spaces is None:
            logger.warning("Could not find Spaces element in GetConfigurationOptions response")
            return soap_response

        # Find RelativePanTiltTranslationSpace
        rel_pt_space = spaces.find('{http://www.onvif.org/ver10/schema}RelativePanTiltTranslationSpace')
        if rel_pt_space is None:
            logger.warning("Could not find RelativePanTiltTranslationSpace")
            return soap_response

        # Check if FOV space already exists
        for space in spaces.findall('{http://www.onvif.org/ver10/schema}RelativePanTiltTranslationSpace'):
            uri = space.find('{http://www.onvif.org/ver10/schema}URI')
            if uri is not None and 'TranslationSpaceFov' in uri.text:
                logger.info("FOV space already exists, skipping")
                return soap_response

        # Create new FOV space element (copy existing GenericSpace and modify)
        fov_space = etree.Element('{http://www.onvif.org/ver10/schema}RelativePanTiltTranslationSpace')

        uri_elem = etree.SubElement(fov_space, '{http://www.onvif.org/ver10/schema}URI')
        uri_elem.text = 'http://www.onvif.org/ver10/tptz/PanTiltSpaces/TranslationSpaceFov'

        xrange = etree.SubElement(fov_space, '{http://www.onvif.org/ver10/schema}XRange')
        xmin = etree.SubElement(xrange, '{http://www.onvif.org/ver10/schema}Min')
        xmin.text = '-1'
        xmax = etree.SubElement(xrange, '{http://www.onvif.org/ver10/schema}Max')
        xmax.text = '1'

        yrange = etree.SubElement(fov_space, '{http://www.onvif.org/ver10/schema}YRange')
        ymin = etree.SubElement(yrange, '{http://www.onvif.org/ver10/schema}Min')
        ymin.text = '-1'
        ymax = etree.SubElement(yrange, '{http://www.onvif.org/ver10/schema}Max')
        ymax.text = '1'

        # Insert the new FOV space element
        # Find the index after the last RelativePanTiltTranslationSpace
        last_rel_pt_idx = None
        for idx, child in enumerate(spaces):
            if child.tag == '{http://www.onvif.org/ver10/schema}RelativePanTiltTranslationSpace':
                last_rel_pt_idx = idx

        if last_rel_pt_idx is not None:
            spaces.insert(last_rel_pt_idx + 1, fov_space)
        else:
            spaces.append(fov_space)

        # Convert back to string
        modified_response = etree.tostring(root, encoding='utf-8', xml_declaration=True).decode('utf-8')
        logger.info("Successfully added FOV space to GetConfigurationOptions")

        return modified_response

    except Exception as e:
        logger.error(f"Error adding FOV to config options: {e}", exc_info=True)
        return soap_response


def initialize_components():
    """Initialize all proxy components."""
    global camera, ptz_wrapper, service_proxy, ptz_interceptor

    logger.info("="*60)
    logger.info("ONVIF Proxy Server - Initializing")
    logger.info("="*60)

    # Connect to camera
    logger.info(f"Connecting to camera at {CAMERA_IP}:{CAMERA_PORT}")
    try:
        camera = ONVIFCamera(CAMERA_IP, CAMERA_PORT, CAMERA_USER, CAMERA_PASS, WSDL_DIR)
        logger.info("Camera connected")
    except Exception as e:
        logger.error(f"Failed to connect to camera: {e}")
        raise

    # Create PTZ wrapper
    logger.info("Creating PTZ wrapper with MoveStatus tracking")
    try:
        ptz_wrapper = ONVIFPTZWrapper(camera)
        logger.info(f"PTZ wrapper created (profile: {ptz_wrapper.profile_token})")
    except Exception as e:
        logger.error(f"Failed to create PTZ wrapper: {e}")
        raise

    # Create service proxy
    logger.info("Creating ONVIF service proxy")
    service_proxy = ONVIFServiceProxy(
        CAMERA_IP, CAMERA_PORT, CAMERA_USER, CAMERA_PASS,
        PROXY_EXTERNAL_HOST, PROXY_PORT
    )
    logger.info("Service proxy created")

    # Create PTZ interceptor
    logger.info("Creating PTZ interceptor")
    ptz_interceptor = PTZInterceptor(ptz_wrapper)
    logger.info("PTZ interceptor created")

    logger.info("="*60)
    logger.info("Initialization complete!")
    logger.info("="*60)


# ============================================================
# ROUTES
# ============================================================

@app.route('/')
def index():
    """Information page."""
    return f"""
    <html>
    <head>
        <title>ONVIF Proxy Server</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 40px; }}
            h1 {{ color: #333; }}
            .info {{ background: #f0f0f0; padding: 15px; border-radius: 5px; margin: 10px 0; }}
            .code {{ background: #282c34; color: #abb2bf; padding: 15px; border-radius: 5px;
                     font-family: monospace; white-space: pre; overflow-x: auto; }}
            .success {{ color: green; }}
            .warning {{ color: orange; }}
        </style>
    </head>
    <body>
        <h1>ONVIF Proxy Server with MoveStatus Support</h1>

        <div class="info">
            <h2>Status</h2>
            <p class="success">Proxy server is running</p>
            <p class="success">PTZ wrapper active (profile: {ptz_wrapper.profile_token if ptz_wrapper else 'N/A'})</p>
        </div>

        <div class="info">
            <h2>Configuration</h2>
            <ul>
                <li><strong>Proxy Address:</strong> {PROXY_EXTERNAL_HOST}:{PROXY_PORT}</li>
                <li><strong>Target Camera:</strong> {CAMERA_IP}:{CAMERA_PORT}</li>
                <li><strong>Username:</strong> {CAMERA_USER}</li>
            </ul>
        </div>

        <div class="info">
            <h2>Features</h2>
            <ul>
                <li>GetStatus with MoveStatus tracking (IDLE/MOVING)</li>
                <li>ContinuousMove with automatic status tracking</li>
                <li>RelativeMove with status tracking</li>
                <li>AbsoluteMove with status tracking</li>
                <li>Stop command tracking</li>
                <li>All other ONVIF services proxied transparently</li>
            </ul>
        </div>

        <div class="info">
            <h2>Frigate Configuration</h2>
            <p>Add this to your Frigate config.yml:</p>
            <div class="code">cameras:
  your_camera:
    onvif:
      host: {PROXY_EXTERNAL_HOST}
      port: {PROXY_PORT}
      user: {CAMERA_USER}
      password: {CAMERA_PASS}
    # ... rest of config
            </div>
        </div>

        <div class="info">
            <h2>Python ONVIF Client Usage</h2>
            <div class="code">from onvif import ONVIFCamera

mycam = ONVIFCamera('{PROXY_EXTERNAL_HOST}', {PROXY_PORT}, '{CAMERA_USER}', '{CAMERA_PASS}', wsdl_dir)
ptz = mycam.create_ptz_service()

status = ptz.GetStatus({{'ProfileToken': '{ptz_wrapper.profile_token if ptz_wrapper else 'PROFILE_TOKEN'}'}})
print(status.MoveStatus.PanTilt)  # IDLE or MOVING
print(status.MoveStatus.Zoom)     # IDLE or MOVING
            </div>
        </div>

        <div class="info">
            <h2>Logs</h2>
            <p>Check the console where you started the proxy for detailed logs.</p>
        </div>
    </body>
    </html>
    """


@app.route('/onvif/<service>', methods=['POST'])
def handle_onvif_request(service):
    """
    Handle ONVIF SOAP requests.

    This is the main entry point for all ONVIF requests from clients.
    """
    # Get SOAP body
    soap_body = request.data.decode('utf-8')

    logger.debug(f"Received request for service: {service}")
    logger.debug(f"Request body: {soap_body[:200]}...")

    # Parse SOAP request
    operation, root = parse_soap_request(soap_body)

    if operation:
        logger.info(f"Operation: {operation} on {service}")
    else:
        logger.warning(f"Could not parse operation from request")

    # Normalize service name (handle both singular and plural forms)
    normalized_service = service.rstrip('s') + '_service' if not service.endswith('_service') else service
    if service in ['ptz_services', 'ptz_service']:
        normalized_service = 'ptz_service'
    elif service in ['media_services', 'media_service']:
        normalized_service = 'media_service'
    elif service in ['device_services', 'device_service']:
        normalized_service = 'device_service'
    elif service in ['event_services', 'event_service']:
        normalized_service = 'events_service'

    # Intercept PTZ commands if this is PTZ service
    if normalized_service == 'ptz_service' and operation and ptz_interceptor:
        intercepted_response, status_code = ptz_interceptor.intercept(operation, root)

        if intercepted_response is not None:
            logger.info(f"Intercepted {operation} - returning tracked response")
            return Response(
                intercepted_response,
                status=status_code,
                mimetype='application/soap+xml; charset=utf-8'
            )

    # Not intercepted - proxy to actual camera
    logger.info(f"Proxying {operation} to camera")
    response_text, status_code = service_proxy.forward_request(normalized_service, soap_body)

    # Post-process GetConfigurationOptions to add FOV support
    if normalized_service == 'ptz_service' and operation == 'GetConfigurationOptions':
        logger.info("Post-processing GetConfigurationOptions to add FOV support")
        response_text = add_fov_to_config_options(response_text)

    return Response(
        response_text,
        status=status_code,
        mimetype='application/soap+xml; charset=utf-8'
    )


@app.route('/health')
def health():
    """Health check endpoint."""
    return {
        'status': 'healthy',
        'camera_connected': camera is not None,
        'ptz_wrapper_active': ptz_wrapper is not None,
        'profile_token': ptz_wrapper.profile_token if ptz_wrapper else None
    }


# ============================================================
# MAIN
# ============================================================

if __name__ == '__main__':
    try:
        # Initialize components
        initialize_components()

        # Start server
        logger.info("")
        logger.info("="*60)
        logger.info(f"Starting ONVIF Proxy Server")
        logger.info(f"Listening on: {PROXY_HOST}:{PROXY_PORT}")
        logger.info(f"External address: {PROXY_EXTERNAL_HOST}:{PROXY_PORT}")
        logger.info(f"Proxying to: {CAMERA_IP}:{CAMERA_PORT}")
        logger.info("")
        logger.info("Connect Frigate to:")
        logger.info(f"  host: {PROXY_EXTERNAL_HOST}")
        logger.info(f"  port: {PROXY_PORT}")
        logger.info(f"  user: {CAMERA_USER}")
        logger.info(f"  pass: {CAMERA_PASS}")
        logger.info("="*60)
        logger.info("")

        # Run Flask app
        app.run(
            host=PROXY_HOST,
            port=PROXY_PORT,
            debug=False,
            threaded=True
        )

    except KeyboardInterrupt:
        logger.info("\nShutting down...")
        if ptz_wrapper:
            ptz_wrapper.cleanup()
        logger.info("Goodbye!")

    except Exception as e:
        logger.error(f"\nFailed to start proxy server: {e}", exc_info=True)
        sys.exit(1)
