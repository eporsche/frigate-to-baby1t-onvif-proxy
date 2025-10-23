#!/usr/bin/env python3
"""
SOAP message handling for ONVIF proxy.

This module handles parsing and building SOAP messages for ONVIF services.
"""

from lxml import etree
from datetime import datetime, timedelta
import re
from typing import Dict, Any, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

# ONVIF namespaces
NAMESPACES = {
    'soap': 'http://www.w3.org/2003/05/soap-envelope',
    'soap12': 'http://www.w3.org/2003/05/soap-envelope',
    'wsdl': 'http://schemas.xmlsoap.org/wsdl/',
    'tds': 'http://www.onvif.org/ver10/device/wsdl',
    'trt': 'http://www.onvif.org/ver10/media/wsdl',
    'tptz': 'http://www.onvif.org/ver20/ptz/wsdl',
    'tt': 'http://www.onvif.org/ver10/schema',
    'ter': 'http://www.onvif.org/ver10/error',
}


def parse_soap_request(soap_body: str) -> Tuple[Optional[str], Optional[etree.Element]]:
    """
    Parse SOAP request and extract operation name.

    Args:
        soap_body: Raw SOAP XML string

    Returns:
        Tuple of (operation_name, xml_root)
    """
    try:
        root = etree.fromstring(soap_body.encode())

        # Find the operation (first element in Body)
        body = root.find('.//{http://www.w3.org/2003/05/soap-envelope}Body')
        if body is None:
            body = root.find('.//{http://schemas.xmlsoap.org/soap/envelope/}Body')

        if body is not None and len(body) > 0:
            operation_elem = body[0]
            operation = etree.QName(operation_elem).localname
            return operation, root

        return None, root

    except Exception as e:
        logger.error(f"Error parsing SOAP request: {e}")
        return None, None


def find_element_by_localname(element, localname: str):
    """Find first element with matching local name (ignoring namespace)."""
    if element is None:
        return None

    # Search through all descendants
    for elem in element.iter():
        if elem.tag.endswith('}' + localname) or elem.tag == localname:
            return elem
    return None


def extract_text(element, xpath: str, namespaces: Dict = None) -> Optional[str]:
    """Extract text from XML element using xpath."""
    if element is None:
        return None

    ns = namespaces or NAMESPACES
    found = element.find(xpath, ns)
    if found is None:
        # Try without namespace
        tag = xpath.split(":")[-1]
        found = find_element_by_localname(element, tag)
    return found.text if found is not None else None


def extract_attr(element, xpath: str, attr: str, namespaces: Dict = None) -> Optional[str]:
    """Extract attribute from XML element using xpath."""
    if element is None:
        return None

    ns = namespaces or NAMESPACES
    found = element.find(xpath, ns)
    if found is None:
        # Try without namespace
        tag = xpath.split(":")[-1]
        found = find_element_by_localname(element, tag)
    return found.get(attr) if found is not None else None


def parse_ptz_getstatus(root: etree.Element) -> Dict[str, Any]:
    """
    Parse GetStatus SOAP request.

    Args:
        root: XML root element

    Returns:
        Dictionary with ProfileToken
    """
    profile_token = extract_text(root, './/tptz:ProfileToken')
    if not profile_token:
        elem = find_element_by_localname(root, 'ProfileToken')
        profile_token = elem.text if elem is not None else None

    return {
        'ProfileToken': profile_token
    }


def parse_ptz_continuous_move(root: etree.Element) -> Dict[str, Any]:
    """
    Parse ContinuousMove SOAP request.

    Args:
        root: XML root element

    Returns:
        Dictionary with ProfileToken, Velocity, and Timeout
    """
    profile_token = extract_text(root, './/tptz:ProfileToken')
    if not profile_token:
        elem = find_element_by_localname(root, 'ProfileToken')
        profile_token = elem.text if elem is not None else None

    velocity = {}

    # Parse PanTilt velocity
    pan_tilt = find_element_by_localname(root, 'PanTilt')
    if pan_tilt is not None:
        velocity['PanTilt'] = {
            'x': float(pan_tilt.get('x', 0)),
            'y': float(pan_tilt.get('y', 0)),
            'space': pan_tilt.get('space', '')
        }

    # Parse Zoom velocity
    zoom = find_element_by_localname(root, 'Zoom')
    if zoom is not None:
        velocity['Zoom'] = {
            'x': float(zoom.get('x', 0)),
            'space': zoom.get('space', '')
        }

    # Parse Timeout
    timeout = None
    timeout_elem = find_element_by_localname(root, 'Timeout')
    if timeout_elem is not None and timeout_elem.text:
        # Parse ISO 8601 duration (PT2S = 2 seconds, PT1.5S = 1.5 seconds)
        match = re.search(r'PT(\d+(?:\.\d+)?)S', timeout_elem.text)
        if match:
            timeout = float(match.group(1))

    return {
        'ProfileToken': profile_token,
        'Velocity': velocity,
        'Timeout': timeout
    }


def parse_ptz_relative_move(root: etree.Element) -> Dict[str, Any]:
    """
    Parse RelativeMove SOAP request.

    Args:
        root: XML root element

    Returns:
        Dictionary with ProfileToken and Translation
    """
    profile_token = extract_text(root, './/tptz:ProfileToken')
    if not profile_token:
        elem = find_element_by_localname(root, 'ProfileToken')
        profile_token = elem.text if elem is not None else None

    translation = {}

    # Find Translation element first
    translation_elem = find_element_by_localname(root, 'Translation')
    if translation_elem is not None:
        # Parse PanTilt translation
        pan_tilt = find_element_by_localname(translation_elem, 'PanTilt')
        if pan_tilt is not None:
            translation['PanTilt'] = {
                'x': float(pan_tilt.get('x', 0)),
                'y': float(pan_tilt.get('y', 0)),
                'space': pan_tilt.get('space', '')
            }

        # Parse Zoom translation
        zoom = find_element_by_localname(translation_elem, 'Zoom')
        if zoom is not None:
            translation['Zoom'] = {
                'x': float(zoom.get('x', 0)),
                'space': zoom.get('space', '')
            }

    return {
        'ProfileToken': profile_token,
        'Translation': translation
    }


def parse_ptz_absolute_move(root: etree.Element) -> Dict[str, Any]:
    """
    Parse AbsoluteMove SOAP request.

    Args:
        root: XML root element

    Returns:
        Dictionary with ProfileToken and Position
    """
    profile_token = extract_text(root, './/tptz:ProfileToken')
    if not profile_token:
        elem = find_element_by_localname(root, 'ProfileToken')
        profile_token = elem.text if elem is not None else None

    position = {}

    # Find Position element first
    position_elem = find_element_by_localname(root, 'Position')
    if position_elem is not None:
        # Parse PanTilt position
        pan_tilt = find_element_by_localname(position_elem, 'PanTilt')
        if pan_tilt is not None:
            position['PanTilt'] = {
                'x': float(pan_tilt.get('x', 0)),
                'y': float(pan_tilt.get('y', 0)),
                'space': pan_tilt.get('space', '')
            }

        # Parse Zoom position
        zoom = find_element_by_localname(position_elem, 'Zoom')
        if zoom is not None:
            position['Zoom'] = {
                'x': float(zoom.get('x', 0)),
                'space': zoom.get('space', '')
            }

    return {
        'ProfileToken': profile_token,
        'Position': position
    }


def parse_ptz_stop(root: etree.Element) -> Dict[str, Any]:
    """
    Parse Stop SOAP request.

    Args:
        root: XML root element

    Returns:
        Dictionary with ProfileToken, PanTilt, and Zoom flags
    """
    profile_token = extract_text(root, './/tptz:ProfileToken')
    if not profile_token:
        elem = find_element_by_localname(root, 'ProfileToken')
        profile_token = elem.text if elem is not None else None

    pan_tilt_text = extract_text(root, './/tptz:PanTilt')
    zoom_text = extract_text(root, './/tptz:Zoom')

    return {
        'ProfileToken': profile_token,
        'PanTilt': pan_tilt_text.lower() == 'true' if pan_tilt_text else True,
        'Zoom': zoom_text.lower() == 'true' if zoom_text else True
    }


def build_ptz_status_response(status) -> str:
    """
    Build GetStatus SOAP response.

    Args:
        status: PTZStatus object from wrapper

    Returns:
        SOAP XML response string
    """
    utc_time = status.UTCTime.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

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
                <tt:UTCTime>{utc_time}</tt:UTCTime>
            </tptz:PTZStatus>
        </tptz:GetStatusResponse>
    </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""

    return response


def build_simple_response(operation: str, namespace: str = 'tptz') -> str:
    """
    Build simple empty SOAP response.

    Args:
        operation: Operation name (e.g., 'ContinuousMove')
        namespace: XML namespace prefix

    Returns:
        SOAP XML response string
    """
    ns_url = NAMESPACES.get(namespace, NAMESPACES['tptz'])

    response = f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"
                   xmlns:{namespace}="{ns_url}">
    <SOAP-ENV:Body>
        <{namespace}:{operation}Response/>
    </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""

    return response


def build_fault_response(faultcode: str, faultstring: str, detail: str = '') -> str:
    """
    Build SOAP Fault response.

    Args:
        faultcode: Fault code
        faultstring: Fault description
        detail: Additional detail

    Returns:
        SOAP Fault XML string
    """
    detail_elem = f'<detail>{detail}</detail>' if detail else ''

    response = f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope">
    <SOAP-ENV:Body>
        <SOAP-ENV:Fault>
            <SOAP-ENV:Code>
                <SOAP-ENV:Value>{faultcode}</SOAP-ENV:Value>
            </SOAP-ENV:Code>
            <SOAP-ENV:Reason>
                <SOAP-ENV:Text xml:lang="en">{faultstring}</SOAP-ENV:Text>
            </SOAP-ENV:Reason>
            {detail_elem}
        </SOAP-ENV:Fault>
    </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""

    return response


def build_service_capabilities_response() -> str:
    """
    Build GetServiceCapabilities response with MoveStatus support.

    Returns:
        SOAP XML response string
    """
    response = """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"
                   xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"
                   xmlns:tt="http://www.onvif.org/ver10/schema">
    <SOAP-ENV:Body>
        <tptz:GetServiceCapabilitiesResponse>
            <tptz:Capabilities EFlip="false" Reverse="false" GetCompatibleConfigurations="true" MoveStatus="true"/>
        </tptz:GetServiceCapabilitiesResponse>
    </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""

    return response
