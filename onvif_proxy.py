#!/usr/bin/env python3
"""
ONVIF service proxy for forwarding requests to actual camera.

This module handles proxying SOAP requests to the real camera
and rewriting addresses in requests/responses.
"""

import requests
from requests.auth import HTTPDigestAuth
import logging
from typing import Tuple

logger = logging.getLogger(__name__)


class ONVIFServiceProxy:
    """Proxy for ONVIF services that forwards requests to actual camera."""

    def __init__(self, camera_ip: str, camera_port: int, camera_user: str,
                 camera_pass: str, proxy_host: str = 'localhost',
                 proxy_port: int = 8000):
        """
        Initialize ONVIF service proxy.

        Args:
            camera_ip: Actual camera IP address
            camera_port: Actual camera ONVIF port
            camera_user: Camera username
            camera_pass: Camera password
            proxy_host: Proxy server hostname
            proxy_port: Proxy server port
        """
        self.camera_ip = camera_ip
        self.camera_port = camera_port
        self.camera_user = camera_user
        self.camera_pass = camera_pass
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port

        self.auth = HTTPDigestAuth(camera_user, camera_pass)

        # Service URL mapping
        self.service_urls = {
            'device_service': f'http://{camera_ip}:{camera_port}/onvif/device_service',
            'media_service': f'http://{camera_ip}:{camera_port}/onvif/media_service',
            'ptz_service': f'http://{camera_ip}:{camera_port}/onvif/ptz_service',
            'imaging_service': f'http://{camera_ip}:{camera_port}/onvif/imaging_service',
            'events_service': f'http://{camera_ip}:{camera_port}/onvif/events_service',
        }

    def rewrite_request(self, soap_body: str) -> str:
        """
        Rewrite proxy addresses in SOAP request to camera addresses.

        Args:
            soap_body: Original SOAP request

        Returns:
            Rewritten SOAP request
        """
        # Replace proxy host with camera host
        soap_body = soap_body.replace(self.proxy_host, self.camera_ip)
        soap_body = soap_body.replace(f':{self.proxy_port}/', f':{self.camera_port}/')
        soap_body = soap_body.replace(f':{self.proxy_port}<', f':{self.camera_port}<')

        return soap_body

    def rewrite_response(self, soap_response: str) -> str:
        """
        Rewrite camera addresses in SOAP response to proxy addresses.

        Args:
            soap_response: Original SOAP response from camera

        Returns:
            Rewritten SOAP response
        """
        # Replace camera host with proxy host
        soap_response = soap_response.replace(self.camera_ip, self.proxy_host)
        soap_response = soap_response.replace(f':{self.camera_port}/', f':{self.proxy_port}/')
        soap_response = soap_response.replace(f':{self.camera_port}<', f':{self.proxy_port}<')

        return soap_response

    def forward_request(self, service: str, soap_body: str,
                       timeout: int = 10) -> Tuple[str, int]:
        """
        Forward SOAP request to actual camera.

        Args:
            service: Service name (e.g., 'ptz_service')
            soap_body: SOAP request body
            timeout: Request timeout in seconds

        Returns:
            Tuple of (response_text, status_code)
        """
        # Get service URL
        service_url = self.service_urls.get(service)
        if not service_url:
            logger.warning(f"Unknown service: {service}, using default URL")
            service_url = f'http://{self.camera_ip}:{self.camera_port}/onvif/{service}'

        # Rewrite addresses in request
        rewritten_body = self.rewrite_request(soap_body)

        # Prepare headers
        headers = {
            'Content-Type': 'application/soap+xml; charset=utf-8',
            'User-Agent': 'ONVIF-Proxy/1.0',
        }

        try:
            logger.debug(f"Forwarding to {service_url}")

            response = requests.post(
                service_url,
                data=rewritten_body,
                headers=headers,
                auth=self.auth,
                timeout=timeout
            )

            # Rewrite addresses in response
            response_text = self.rewrite_response(response.text)

            return response_text, response.status_code

        except requests.exceptions.Timeout:
            logger.error(f"Timeout forwarding request to {service_url}")
            return self._timeout_fault(), 500

        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error to {service_url}: {e}")
            return self._connection_fault(), 500

        except Exception as e:
            logger.error(f"Error forwarding request: {e}", exc_info=True)
            return self._generic_fault(str(e)), 500

    def _timeout_fault(self) -> str:
        """Generate SOAP fault for timeout."""
        return """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope">
    <SOAP-ENV:Body>
        <SOAP-ENV:Fault>
            <SOAP-ENV:Code>
                <SOAP-ENV:Value>SOAP-ENV:Receiver</SOAP-ENV:Value>
            </SOAP-ENV:Code>
            <SOAP-ENV:Reason>
                <SOAP-ENV:Text xml:lang="en">Request timeout</SOAP-ENV:Text>
            </SOAP-ENV:Reason>
        </SOAP-ENV:Fault>
    </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""

    def _connection_fault(self) -> str:
        """Generate SOAP fault for connection error."""
        return """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope">
    <SOAP-ENV:Body>
        <SOAP-ENV:Fault>
            <SOAP-ENV:Code>
                <SOAP-ENV:Value>SOAP-ENV:Receiver</SOAP-ENV:Value>
            </SOAP-ENV:Code>
            <SOAP-ENV:Reason>
                <SOAP-ENV:Text xml:lang="en">Connection error to camera</SOAP-ENV:Text>
            </SOAP-ENV:Reason>
        </SOAP-ENV:Fault>
    </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""

    def _generic_fault(self, message: str) -> str:
        """Generate generic SOAP fault."""
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope">
    <SOAP-ENV:Body>
        <SOAP-ENV:Fault>
            <SOAP-ENV:Code>
                <SOAP-ENV:Value>SOAP-ENV:Receiver</SOAP-ENV:Value>
            </SOAP-ENV:Code>
            <SOAP-ENV:Reason>
                <SOAP-ENV:Text xml:lang="en">{message}</SOAP-ENV:Text>
            </SOAP-ENV:Reason>
        </SOAP-ENV:Fault>
    </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""
