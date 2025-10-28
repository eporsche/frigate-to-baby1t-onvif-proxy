
# ONVIF Proxy for Baby 1T Babyphone Integration with Frigate

Simple onvif proxy which translates relative moves used by frigate to ContinuousMove used by Baby 1T Babyphone from ieGeek.

# Installation

```shell
python3 -m venv .venv
```

```shell
source .venv\bin\activate
```

```shell
python3 -m pip install -r requirements.txt
```

Copy the .env.example to .env and configure it with your credentials and ip addresses.

# Use with onvif client

```shell
python3 proxy_server.py
```
Dockerfile for onvif proxy included

# Alternative use of http commands

Instead of using the onvif proxy you can also use the direct http interface.

```shell
python3 ptz_server.py
```

## Examples:
  - http://127.0.0.1:5001/ptz/up?speed=0.3&duration=1.0
  - http://127.0.0.1:5001/ptz/down?speed=0.3&duration=1.0
  - http://127.0.0.1:5001/ptz/left?speed=0.3&duration=1.0
  - http://127.0.0.1:5001/ptz/right?speed=0.3&duration=1.0
