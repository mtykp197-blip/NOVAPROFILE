"""Match the browser's location, timezone and language to the proxy exit IP.

The core's own rotating-proxy service does the same thing: it resolves the
exit IP, reads the country, timezone and coordinates, then writes them into
network.hidemium so the fingerprint matches the connection.
"""
import json
import random
from urllib.request import ProxyHandler, build_opener

GEO_URL='http://ip-api.com/json/?fields=status,country,countryCode,timezone,lat,lon,query'
# Hidemium's own fallback: Vietnam, used when the exit IP cannot be resolved.
DEFAULT_LOCATION={'country_code':'VN','country':'Vietnam','timezone':'Asia/Ho_Chi_Minh',
                   'latitude':21.0285,'longitude':105.8542,'languages':'vi-VN,vi,en-US,en'}
VIETNAM_CITIES=[('Ha Noi',21.0285,105.8542),('Ho Chi Minh',10.8231,106.6297),
                ('Da Nang',16.0544,108.2022),('Hai Phong',20.8449,106.6881),
                ('Can Tho',10.0452,105.7469),('Hue',16.4637,107.5843)]


def resolve_exit_geo(proxy_url,timeout=25):
    """Resolve the exit IP and its location through the given proxy."""
    opener=build_opener(ProxyHandler({'http':proxy_url,'https':proxy_url}))
    data=json.loads(opener.open(GEO_URL,timeout=timeout).read().decode('utf8'))
    if data.get('status')!='success':raise ValueError('Khong phat hien duoc vi tri cua proxy')
    return {'country_code':data.get('countryCode') or '','country':data.get('country') or '',
            'timezone':data.get('timezone') or '','latitude':data.get('lat'),
            'longitude':data.get('lon'),'languages':'','ip':data.get('query') or ''}


def random_location(seed=''):
    """A random Vietnamese location, stable for one profile seed."""
    rng=random.Random(seed or '')
    name,lat,lon=rng.choice(VIETNAM_CITIES)
    return {**DEFAULT_LOCATION,'city':name,'latitude':lat,'longitude':lon}


def network_hidemium(geo,public_ip=''):
    """Build network.hidemium: the core reads the IP values as plain literals."""
    lines=[]
    if public_ip:
        lines.append('webrtc.public_ip:'+public_ip)
        lines.append('machine.ip_public:'+public_ip)
    if geo.get('timezone'):lines.append('hidemium.timezone:'+geo['timezone'])
    if geo.get('latitude') is not None:lines.append('hidemium.geolocation.latitude:'+str(geo['latitude']))
    if geo.get('longitude') is not None:lines.append('hidemium.geolocation.longitude:'+str(geo['longitude']))
    if geo.get('languages'):lines.append('hidemium.languages:'+geo['languages'])
    return '\n'.join(lines)+('\n' if lines else '')
