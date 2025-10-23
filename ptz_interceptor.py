#!/usr/bin/env python3
"""
PTZ command interceptor with MoveStatus tracking.

This module intercepts PTZ commands, tracks their status, and translates
them to actual camera commands via the wrapper.
"""

from onvif_ptz_wrapper import ONVIFPTZWrapper
from soap_handler import (
    parse_ptz_getstatus,
    parse_ptz_continuous_move,
    parse_ptz_relative_move,
    parse_ptz_absolute_move,
    parse_ptz_stop,
    build_ptz_status_response,
    build_simple_response,
    build_fault_response,
    build_service_capabilities_response
)
from lxml import etree
from typing import Tuple, Optional
import logging
import threading
import time

logger = logging.getLogger(__name__)


class PTZInterceptor:
    """
    Intercepts PTZ commands to add MoveStatus tracking.

    This class sits between the ONVIF client (Frigate) and the camera,
    tracking all PTZ movements and providing GetStatus with MoveStatus.
    """

    def __init__(self, ptz_wrapper: ONVIFPTZWrapper):
        """
        Initialize PTZ interceptor.

        Args:
            ptz_wrapper: ONVIFPTZWrapper instance for tracking
        """
        self.ptz_wrapper = ptz_wrapper

    def intercept(self, operation: str, root: etree.Element) -> Tuple[Optional[str], Optional[int]]:
        """
        Intercept PTZ operation and track status.

        Args:
            operation: PTZ operation name
            root: Parsed XML root element

        Returns:
            Tuple of (soap_response, status_code) or (None, None) if not intercepted
        """
        try:
            if operation == 'GetServiceCapabilities':
                return self._handle_get_service_capabilities(root)

            elif operation == 'GetConfigurationOptions':
                # Let this pass through for now, we'll modify the response
                return None, None

            elif operation == 'GetStatus':
                return self._handle_get_status(root)

            elif operation == 'ContinuousMove':
                return self._handle_continuous_move(root)

            elif operation == 'RelativeMove':
                return self._handle_relative_move(root)

            elif operation == 'AbsoluteMove':
                return self._handle_absolute_move(root)

            elif operation == 'Stop':
                return self._handle_stop(root)

            # Not a tracked operation
            return None, None

        except Exception as e:
            logger.error(f"Error intercepting {operation}: {e}", exc_info=True)
            fault = build_fault_response(
                'SOAP-ENV:Receiver',
                f'Error processing {operation}',
                str(e)
            )
            return fault, 500

    def _handle_get_service_capabilities(self, root: etree.Element) -> Tuple[str, int]:
        """
        Handle GetServiceCapabilities - report MoveStatus support.

        Args:
            root: Parsed XML root

        Returns:
            Tuple of (soap_response, status_code)
        """
        logger.info("Intercepting GetServiceCapabilities - reporting MoveStatus support")

        try:
            # Build SOAP response with MoveStatus capability
            response = build_service_capabilities_response()

            return response, 200

        except Exception as e:
            logger.error(f"Error in GetServiceCapabilities: {e}", exc_info=True)
            raise

    def _handle_get_status(self, root: etree.Element) -> Tuple[str, int]:
        """
        Handle GetStatus request with MoveStatus tracking.

        Args:
            root: Parsed XML root

        Returns:
            Tuple of (soap_response, status_code)
        """
        logger.info("Intercepting GetStatus - returning tracked status")

        try:
            # Parse request
            params = parse_ptz_getstatus(root)

            # Get status from wrapper (with MoveStatus tracking)
            status = self.ptz_wrapper.GetStatus(params)

            # Log the status details
            logger.debug(f"GetStatus Response Details:")
            logger.debug(f"  Position PanTilt: x={status.Position.PanTilt['x']:.3f}, y={status.Position.PanTilt['y']:.3f}")
            logger.debug(f"  Position Zoom: x={status.Position.Zoom['x']:.3f}")
            logger.debug(f"  MoveStatus PanTilt: {status.MoveStatus.PanTilt}")
            logger.debug(f"  MoveStatus Zoom: {status.MoveStatus.Zoom}")
            logger.debug(f"  UTCTime: {status.UTCTime}")

            # Build SOAP response
            response = build_ptz_status_response(status)

            # Log part of the response for debugging
            logger.debug(f"SOAP Response (first 500 chars): {response[:500]}")

            return response, 200

        except Exception as e:
            logger.error(f"Error in GetStatus: {e}", exc_info=True)
            raise

    def _handle_continuous_move(self, root: etree.Element) -> Tuple[str, int]:
        """
        Handle ContinuousMove request.

        Args:
            root: Parsed XML root

        Returns:
            Tuple of (soap_response, status_code)
        """
        logger.info("Intercepting ContinuousMove")

        try:
            # Parse request
            params = parse_ptz_continuous_move(root)

            logger.debug(f"ContinuousMove params: {params}")

            # Execute via wrapper (tracks status automatically)
            self.ptz_wrapper.ContinuousMove(params)

            # Build success response
            response = build_simple_response('ContinuousMove', 'tptz')

            return response, 200

        except Exception as e:
            logger.error(f"Error in ContinuousMove: {e}", exc_info=True)
            raise

    def _handle_relative_move(self, root: etree.Element) -> Tuple[str, int]:
        """
        Handle RelativeMove request - translate Pan/Tilt to ContinuousMove.

        RelativeMove Pan/Tilt is not supported by the camera, but we can
        simulate it using ContinuousMove with a calculated duration.

        Args:
            root: Parsed XML root

        Returns:
            Tuple of (soap_response, status_code)
        """
        logger.info("Intercepting RelativeMove")

        try:
            # Parse request
            params = parse_ptz_relative_move(root)

            logger.debug(f"RelativeMove params: {params}")

            # Check if this is Pan/Tilt relative move
            translation = params.get('Translation', {})
            pan_tilt = translation.get('PanTilt')
            zoom = translation.get('Zoom')

            if pan_tilt:
                # Translate Pan/Tilt RelativeMove to ContinuousMove
                logger.info(f"Translating Pan/Tilt RelativeMove to ContinuousMove: {pan_tilt}")

                # Extract translation values
                x_trans = pan_tilt.get('x', 0)
                y_trans = pan_tilt.get('y', 0)

                # Use fixed velocity like ptz_server.py does
                # Sign of velocity matches sign of translation
                velocity_x = 0.5 if x_trans > 0 else (-0.5 if x_trans < 0 else 0)
                velocity_y = 0.5 if y_trans > 0 else (-0.5 if y_trans < 0 else 0)

                # Calculate duration based on translation distance
                # Scale the duration by the translation amount
                # Increase multiplier (10.0) for longer movements / bigger steps
                duration = max(0.3, min(5.0, abs(x_trans) * 10.0 + abs(y_trans) * 10.0))

                logger.info(f"Starting ContinuousMove: velocity=({velocity_x:.2f}, {velocity_y:.2f}), duration={duration:.2f}s")

                # Start continuous movement (like ptz_server.py line 34-40)
                start_params = {
                    'ProfileToken': params.get('ProfileToken'),
                    'Velocity': {
                        'PanTilt': {'x': velocity_x, 'y': velocity_y},
                        'Zoom': {'x': 0}
                    }
                }
                self.ptz_wrapper.ContinuousMove(start_params)

                # Schedule stop movement (like ptz_server.py line 43-51)
                def stop_movement():
                    time.sleep(duration)
                    stop_params = {
                        'ProfileToken': params.get('ProfileToken'),
                        'Velocity': {
                            'PanTilt': {'x': 0, 'y': 0},
                            'Zoom': {'x': 0}
                        }
                    }
                    logger.info(f"Stopping movement with velocity (0, 0)")
                    self.ptz_wrapper.ContinuousMove(stop_params)

                threading.Thread(target=stop_movement, daemon=True).start()

            elif zoom:
                # Zoom relative move - pass through (camera supports this)
                logger.info("Zoom RelativeMove - passing through to camera")
                self.ptz_wrapper.RelativeMove(params)

            else:
                logger.warning("RelativeMove with no Pan/Tilt or Zoom - ignoring")

            # Build success response
            response = build_simple_response('RelativeMove', 'tptz')

            return response, 200

        except Exception as e:
            logger.error(f"Error in RelativeMove: {e}", exc_info=True)
            raise

    def _handle_absolute_move(self, root: etree.Element) -> Tuple[str, int]:
        """
        Handle AbsoluteMove request.

        Args:
            root: Parsed XML root

        Returns:
            Tuple of (soap_response, status_code)
        """
        logger.info("Intercepting AbsoluteMove")

        try:
            # Parse request
            params = parse_ptz_absolute_move(root)

            logger.debug(f"AbsoluteMove params: {params}")

            # Execute via wrapper (tracks status automatically)
            self.ptz_wrapper.AbsoluteMove(params)

            # Build success response
            response = build_simple_response('AbsoluteMove', 'tptz')

            return response, 200

        except Exception as e:
            logger.error(f"Error in AbsoluteMove: {e}", exc_info=True)
            raise

    def _handle_stop(self, root: etree.Element) -> Tuple[str, int]:
        """
        Handle Stop request - translate to ContinuousMove with velocity (0,0).

        Camera doesn't support Stop command, so we simulate it by sending
        ContinuousMove with zero velocity (like ptz_server.py does).

        Args:
            root: Parsed XML root

        Returns:
            Tuple of (soap_response, status_code)
        """
        logger.info("Intercepting Stop - translating to ContinuousMove(0,0)")

        try:
            # Parse request
            params = parse_ptz_stop(root)

            logger.debug(f"Stop params: {params}")

            # Translate Stop to ContinuousMove with velocity (0,0)
            # This is how ptz_server.py stops movement (line 45-51)
            stop_params = {
                'ProfileToken': params.get('ProfileToken'),
                'Velocity': {
                    'PanTilt': {'x': 0, 'y': 0},
                    'Zoom': {'x': 0}
                }
            }

            logger.info("Sending ContinuousMove with velocity (0, 0) to stop movement")
            self.ptz_wrapper.ContinuousMove(stop_params)

            # Build success response
            response = build_simple_response('Stop', 'tptz')

            return response, 200

        except Exception as e:
            logger.error(f"Error in Stop: {e}", exc_info=True)
            raise
