#!/usr/bin/env python3
"""
ONVIF PTZ Wrapper with MoveStatus and GetStatus support.

This wrapper adds status tracking for cameras that don't natively support
MoveStatus. It tracks movements initiated through ContinuousMove, RelativeMove,
and AbsoluteMove, and provides a GetStatus implementation that reports IDLE/MOVING.
"""

from onvif import ONVIFCamera
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class PTZStatus:
    """Simulated PTZ status object matching ONVIF PTZ status structure."""

    def __init__(self):
        self.MoveStatus = MoveStatus()
        self.Position = Position()
        self.UTCTime = datetime.utcnow()

    def __repr__(self):
        return (f"PTZStatus(MoveStatus={self.MoveStatus}, "
                f"Position={self.Position}, UTCTime={self.UTCTime})")


class MoveStatus:
    """Simulated MoveStatus object."""

    def __init__(self):
        self.PanTilt = "IDLE"
        self.Zoom = "IDLE"

    def __repr__(self):
        return f"MoveStatus(PanTilt={self.PanTilt}, Zoom={self.Zoom})"


class Position:
    """Simulated Position object."""

    def __init__(self):
        self.PanTilt = {'x': 0.0, 'y': 0.0}
        self.Zoom = {'x': 0.0}

    def __repr__(self):
        return f"Position(PanTilt={self.PanTilt}, Zoom={self.Zoom})"


class ONVIFPTZWrapper:
    """
    Wrapper for ONVIF PTZ service that adds MoveStatus and GetStatus tracking.

    This wrapper intercepts PTZ movement commands and tracks their status,
    providing GetStatus functionality for cameras that don't natively support it.
    """

    def __init__(self, camera: ONVIFCamera, profile_token: str = None):
        """
        Initialize the PTZ wrapper.

        Args:
            camera: ONVIFCamera instance
            profile_token: Media profile token to use (auto-selects if None)
        """
        self.camera = camera
        self.ptz_service = camera.create_ptz_service()

        # Get profile token if not provided
        if profile_token is None:
            media = camera.create_media_service()
            profiles = media.GetProfiles()

            # Find first profile with PTZ configuration
            for profile in profiles:
                if profile.PTZConfiguration:
                    profile_token = profile.token
                    break

            if profile_token is None:
                raise ValueError("No PTZ-enabled profile found")

        self.profile_token = profile_token

        # Movement status tracking
        self._status_lock = threading.Lock()
        self._pan_tilt_status = "IDLE"
        self._zoom_status = "IDLE"
        self._estimated_position = Position()
        self._stop_timers: Dict[str, threading.Timer] = {}

        # Check native GetStatus support
        self._native_status_support = self._check_native_status_support()

    def _check_native_status_support(self) -> bool:
        """Check if camera natively supports GetStatus."""
        try:
            request = self.ptz_service.create_type("GetStatus")
            request.ProfileToken = self.profile_token
            status = self.ptz_service.GetStatus(request)

            # Check if MoveStatus exists
            if hasattr(status, 'MoveStatus'):
                logger.info("Camera has native GetStatus support")
                return True
        except Exception as e:
            logger.debug(f"Native GetStatus check failed: {e}")

        logger.info("Using simulated GetStatus")
        return False

    def GetStatus(self, request=None):
        """
        Get PTZ status with MoveStatus tracking.

        Args:
            request: ONVIF GetStatus request (optional)

        Returns:
            PTZStatus object with MoveStatus and Position
        """
        # Try native status first if supported
        if self._native_status_support:
            try:
                if request is None:
                    request = self.ptz_service.create_type("GetStatus")
                    request.ProfileToken = self.profile_token
                return self.ptz_service.GetStatus(request)
            except Exception as e:
                logger.warning(f"Native GetStatus failed, using simulated: {e}")

        # Return simulated status
        with self._status_lock:
            status = PTZStatus()
            status.MoveStatus.PanTilt = self._pan_tilt_status
            status.MoveStatus.Zoom = self._zoom_status
            status.Position.PanTilt = self._estimated_position.PanTilt.copy()
            status.Position.Zoom = self._estimated_position.Zoom.copy()
            status.UTCTime = datetime.utcnow()
            return status

    def _set_pan_tilt_status(self, status: str, duration: float = 0):
        """Set pan/tilt status and optionally schedule return to IDLE."""
        with self._status_lock:
            self._pan_tilt_status = status

        # Cancel existing timer
        if 'pantilt' in self._stop_timers:
            self._stop_timers['pantilt'].cancel()

        # Schedule return to IDLE
        if status == "MOVING" and duration > 0:
            def set_idle():
                with self._status_lock:
                    self._pan_tilt_status = "IDLE"

            timer = threading.Timer(duration, set_idle)
            timer.start()
            self._stop_timers['pantilt'] = timer

    def _set_zoom_status(self, status: str, duration: float = 0):
        """Set zoom status and optionally schedule return to IDLE."""
        with self._status_lock:
            self._zoom_status = status

        # Cancel existing timer
        if 'zoom' in self._stop_timers:
            self._stop_timers['zoom'].cancel()

        # Schedule return to IDLE
        if status == "MOVING" and duration > 0:
            def set_idle():
                with self._status_lock:
                    self._zoom_status = "IDLE"

            timer = threading.Timer(duration, set_idle)
            timer.start()
            self._stop_timers['zoom'] = timer

    def _update_estimated_position(self, pan_tilt: Dict = None, zoom: Dict = None,
                                   duration: float = 0, velocity: Dict = None):
        """Update estimated position based on movement."""
        with self._status_lock:
            if pan_tilt is not None:
                # Absolute or relative move
                if 'x' in pan_tilt:
                    self._estimated_position.PanTilt['x'] += pan_tilt['x']
                if 'y' in pan_tilt:
                    self._estimated_position.PanTilt['y'] += pan_tilt['y']
            elif velocity is not None and 'PanTilt' in velocity and duration > 0:
                # Continuous move - estimate based on velocity and duration
                vel = velocity['PanTilt']
                if 'x' in vel:
                    self._estimated_position.PanTilt['x'] += vel['x'] * duration * 0.1
                if 'y' in vel:
                    self._estimated_position.PanTilt['y'] += vel['y'] * duration * 0.1

            if zoom is not None and 'x' in zoom:
                self._estimated_position.Zoom['x'] += zoom['x']
            elif velocity is not None and 'Zoom' in velocity and duration > 0:
                vel = velocity['Zoom']
                if 'x' in vel:
                    self._estimated_position.Zoom['x'] += vel['x'] * duration * 0.1

            # Clamp values to [-1, 1] range
            self._estimated_position.PanTilt['x'] = max(-1.0, min(1.0,
                self._estimated_position.PanTilt['x']))
            self._estimated_position.PanTilt['y'] = max(-1.0, min(1.0,
                self._estimated_position.PanTilt['y']))
            self._estimated_position.Zoom['x'] = max(0.0, min(1.0,
                self._estimated_position.Zoom['x']))

    def ContinuousMove(self, request):
        """
        Execute ContinuousMove with status tracking.

        Args:
            request: ONVIF ContinuousMove request with Velocity and ProfileToken (dict or object)
        """
        result = self.ptz_service.ContinuousMove(request)

        # Track movement status - handle both dict and object
        if isinstance(request, dict):
            velocity = request.get('Velocity', {})
            timeout = request.get('Timeout', None)
        else:
            velocity = getattr(request, 'Velocity', {})
            timeout = getattr(request, 'Timeout', None)

        # Convert velocity to dict if it's an object
        if hasattr(velocity, '__dict__'):
            velocity = vars(velocity)

        # Check if this is a stop command (zero velocity)
        is_stop = True
        pan_tilt = velocity.get('PanTilt') if isinstance(velocity, dict) else getattr(velocity, 'PanTilt', None)
        zoom = velocity.get('Zoom') if isinstance(velocity, dict) else getattr(velocity, 'Zoom', None)

        if pan_tilt:
            pt_dict = pan_tilt if isinstance(pan_tilt, dict) else {'x': getattr(pan_tilt, 'x', 0), 'y': getattr(pan_tilt, 'y', 0)}
            if pt_dict.get('x', 0) != 0 or pt_dict.get('y', 0) != 0:
                is_stop = False

        if zoom:
            z_dict = zoom if isinstance(zoom, dict) else {'x': getattr(zoom, 'x', 0)}
            if z_dict.get('x', 0) != 0:
                is_stop = False

        if is_stop:
            self._set_pan_tilt_status("IDLE")
            self._set_zoom_status("IDLE")
        else:
            # Get timeout from request or use default
            if timeout:
                if isinstance(timeout, timedelta):
                    duration = timeout.total_seconds()
                else:
                    duration = float(timeout)
            else:
                duration = 5.0  # Default timeout

            # Set moving status with auto-return to IDLE
            if pan_tilt:
                self._set_pan_tilt_status("MOVING", duration)
            if zoom:
                self._set_zoom_status("MOVING", duration)

            # Estimate position change
            self._update_estimated_position(velocity=velocity, duration=duration)

        return result

    def RelativeMove(self, request):
        """
        Execute RelativeMove with status tracking.

        Args:
            request: ONVIF RelativeMove request with Translation and ProfileToken (dict or object)
        """
        result = self.ptz_service.RelativeMove(request)

        # Estimate movement duration (cameras typically take 1-3 seconds)
        duration = 2.0

        # Track movement - handle both dict and object
        if isinstance(request, dict):
            translation = request.get('Translation', {})
        else:
            translation = getattr(request, 'Translation', {})

        # Convert to dict if object
        if hasattr(translation, '__dict__'):
            translation = vars(translation)

        pan_tilt = translation.get('PanTilt') if isinstance(translation, dict) else getattr(translation, 'PanTilt', None)
        zoom = translation.get('Zoom') if isinstance(translation, dict) else getattr(translation, 'Zoom', None)

        if pan_tilt:
            pt_dict = pan_tilt if isinstance(pan_tilt, dict) else {'x': getattr(pan_tilt, 'x', 0), 'y': getattr(pan_tilt, 'y', 0)}
            self._set_pan_tilt_status("MOVING", duration)
            self._update_estimated_position(pan_tilt=pt_dict)

        if zoom:
            z_dict = zoom if isinstance(zoom, dict) else {'x': getattr(zoom, 'x', 0)}
            self._set_zoom_status("MOVING", duration)
            self._update_estimated_position(zoom=z_dict)

        return result

    def AbsoluteMove(self, request):
        """
        Execute AbsoluteMove with status tracking.

        Args:
            request: ONVIF AbsoluteMove request with Position and ProfileToken (dict or object)
        """
        result = self.ptz_service.AbsoluteMove(request)

        # Estimate movement duration
        duration = 3.0

        # Track movement - handle both dict and object
        if isinstance(request, dict):
            position = request.get('Position', {})
        else:
            position = getattr(request, 'Position', {})

        # Convert to dict if object
        if hasattr(position, '__dict__'):
            position = vars(position)

        pan_tilt = position.get('PanTilt') if isinstance(position, dict) else getattr(position, 'PanTilt', None)
        zoom = position.get('Zoom') if isinstance(position, dict) else getattr(position, 'Zoom', None)

        if pan_tilt:
            self._set_pan_tilt_status("MOVING", duration)
            with self._status_lock:
                if isinstance(pan_tilt, dict):
                    self._estimated_position.PanTilt = pan_tilt.copy()
                else:
                    self._estimated_position.PanTilt = {'x': getattr(pan_tilt, 'x', 0), 'y': getattr(pan_tilt, 'y', 0)}

        if zoom:
            self._set_zoom_status("MOVING", duration)
            with self._status_lock:
                if isinstance(zoom, dict):
                    self._estimated_position.Zoom = zoom.copy()
                else:
                    self._estimated_position.Zoom = {'x': getattr(zoom, 'x', 0)}

        return result

    def Stop(self, request):
        """
        Execute Stop command with status tracking.

        Args:
            request: ONVIF Stop request with ProfileToken (dict or object)
        """
        result = self.ptz_service.Stop(request)

        # Check what to stop - handle both dict and object
        if isinstance(request, dict):
            pan_tilt = request.get('PanTilt', True)
            zoom = request.get('Zoom', True)
        else:
            pan_tilt = getattr(request, 'PanTilt', True)
            zoom = getattr(request, 'Zoom', True)

        if pan_tilt:
            self._set_pan_tilt_status("IDLE")
        if zoom:
            self._set_zoom_status("IDLE")

        return result

    def __getattr__(self, name):
        """
        Proxy all other methods to the underlying PTZ service.

        Args:
            name: Method name

        Returns:
            Method from underlying PTZ service
        """
        return getattr(self.ptz_service, name)

    def cleanup(self):
        """Cancel any pending timers."""
        for timer in self._stop_timers.values():
            timer.cancel()
        self._stop_timers.clear()


def create_ptz_wrapper(ip: str, port: int, username: str, password: str,
                      wsdl_dir: str = None, profile_token: str = None) -> ONVIFPTZWrapper:
    """
    Convenience function to create a PTZ wrapper with camera connection.

    Args:
        ip: Camera IP address
        port: ONVIF port
        username: Camera username
        password: Camera password
        wsdl_dir: Optional WSDL directory path
        profile_token: Optional specific profile token

    Returns:
        ONVIFPTZWrapper instance
    """
    if wsdl_dir:
        camera = ONVIFCamera(ip, port, username, password, wsdl_dir)
    else:
        camera = ONVIFCamera(ip, port, username, password)

    return ONVIFPTZWrapper(camera, profile_token)
