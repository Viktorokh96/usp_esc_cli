"""
uSpherum ESC Console Client

Usage:
    cli.py config list
    cli.py config set <config_name> <value>
    cli.py ll list [--sort=<sort by>] [--filter=<filter>] [--group=<group>]
    cli.py ll status [--sort=<sort by>] [--filter=<filter>] [--group=<group>]
    cli.py ll show <id>
    cli.py ll add <name> [--lat <lat>] [--lng <lng>]
    cli.py ll cmd (--relay=<relay> | --mode=<mode>)
    cli.py llgr list
    cli.py (--interactive|-i)
    cli.py show <show>

Options:
    -h --help   Show this help screen
    --lat       Object latitude
    --lng       Object longitude
"""
from json import load, dump, loads, dumps
from uuid import UUID
import pkg_resources
import pickle
import datetime
from urllib.parse import urlparse
from random import randint
from typing import Sequence
from os.path import dirname, join, exists
from collections import OrderedDict, abc
from asyncio import get_event_loop
from urllib.parse import urljoin
from pprint import pprint
from jsonschema import validate
from docopt import docopt, DocoptExit, printable_usage
from tabulate import tabulate
from websockets.client import Connect
import websockets
import requests


def deep_update(d, u):
    for k, v in u.items():
        if isinstance(v, abc.Mapping):
            d[k] = deep_update(d.get(k, {}), v)
        else:
            d[k] = v
    return d


def utc_dt() -> datetime.datetime:
    return datetime.datetime.utcnow().astimezone()


def utc_ts(mult=1e6) -> int:
    """Микросекунды"""
    return int(utc_dt().timestamp()*mult)


def sid() -> int:
    return randint(1, 1 << 128)


class Config:
    config_path = join(dirname(__file__), '.esc-cli.json')
    config_schema = {
        'type': 'object',
        'properties': {
            'api.url': {
                'type': 'string'
            },
            'api.http.auth.token': {
                'type': 'string'
            },
            'api.ws.auth.token': {
                'type': 'string'
            }
        },
        'required': ['api.url', 'api.http.auth.token', 'api.ws.auth.token']
    }

    @classmethod
    def default_config(cls) -> dict:
        return {
            'api.url': 'http://localhost:8000',
            'api.http.auth.token': 'SECRET_AUTH_TOKEN',
            'api.ws.auth.token': 'SECRET_WS_AUTH_TOKEN',
        }

    @classmethod
    def _load_current_config(cls) -> dict:
        cfg = cls.default_config()
        if exists(cls.config_path):
            with open(cls.config_path) as f:
                deep_update(cfg, load(f))

        validate(cfg, cls.config_schema)
        return cfg

    @classmethod
    def list(cls) -> dict:
        return {
            'config.path': cls.config_path,
            **cls._load_current_config()
        }

    @classmethod
    def get(cls, key):
        return cls._load_current_config()[key]

    @classmethod
    def set(cls, key, value) -> dict:
        cfg = cls._load_current_config()
        cfg[key] = value

        validate(cfg, cls.config_schema)
        with open(cls.config_path, 'w') as f:
            dump(cfg, f, indent=4)


class Cache:
    cache_path = '.cli-cache'

    @classmethod
    def _current_cache(cls) -> dict:
        cache = {}

        if exists(cls.cache_path):
            with open(cls.cache_path, 'rb') as cache_file:
                cache = pickle.load(cache_file)
        return cache

    @classmethod
    def get(cls, key, default=None):
        return cls._current_cache().get(key, default)

    @classmethod
    def set(cls, key, value):
        current_cache = cls._current_cache()
        current_cache[key] = value
        with open(cls.cache_path, 'wb') as cache_file:
            pickle.dump(current_cache, cache_file)


def ws_uri_from_url(url: str) -> str:
    url = urlparse(url)
    return dict(https='wss', http='ws')[url.scheme] + '://' + url.netloc


def config_list(args):
    pprint(Config.list())


def config_set(args):
    Config.set(args['<config_name>'], args['<value>'])


def _get_line_state(line_id):
    url = urljoin(Config.get('api.url'), f'api/v1/lighting-line-state')
    token = Config.get('api.http.auth.token')
    lines_state, = tuple(
        filter(
            lambda st: st['line_id'] == line_id,
            requests.get(
                url, headers=dict(Authorization=f'Bearer {token}')).json()
        )
    )
    del lines_state['line_id']
    return lines_state


def _get_line(line_id):
    url = urljoin(Config.get('api.url'), f'api/v1/lighting-line/{line_id}')
    token = Config.get('api.http.auth.token')
    line = requests.get(
        url, headers=dict(Authorization=f'Bearer {token}')).json()
    return line


def _get_lines():
    url = urljoin(Config.get('api.url'), 'api/v1/lighting-line')
    token = Config.get('api.http.auth.token')
    lines = requests.get(
        url, headers=dict(Authorization=f'Bearer {token}')).json()
    return lines


def _get_lines_status():
    url = urljoin(Config.get('api.url'), 'api/v1/lighting-line-state')
    token = Config.get('api.http.auth.token')
    lines_status = requests.get(
        url, headers=dict(Authorization=f'Bearer {token}')).json()
    return lines_status


def _get_line_groups():
    url = urljoin(Config.get('api.url'), 'api/v1/lighting-line-group')
    token = Config.get('api.http.auth.token')
    groups = requests.get(
        url, headers=dict(Authorization=f'Bearer {token}')).json()
    return groups


def apply_line_filters_and_sorting(lines, sort_by, group_id, req_filter):

    def ll_filter(line):
        return True

    if req_filter is not None:
        field_name, filter_expr = req_filter.split(':')
        if field_name in ('id', 'name'):
            def ll_filter(line):
                return line[field_name].find(filter_expr) != -1

        if field_name == 'm' and filter_expr.lower() in ('t', 'f'):
            def ll_filter(line):
                return line['in_maintenance'] == (filter_expr.lower() == 't')

    def ll_group_filter(f):
        def _filter(line):
            if group_id is not None:
                return f(line) and int(group_id) in line['groups']
            return f(line)
        return _filter

    ll_filter = ll_group_filter(ll_filter)

    lines = sorted(
        lines,
        key=lambda l: UUID(l[sort_by]) if sort_by == 'id' else sort_by)

    return [l for l in lines if ll_filter(l)]


def ll_list(args):
    lines = _get_lines()
    sort_by = args['--sort'] or 'id'
    group_id = args['--group']
    required_filter = args['--filter']

    if isinstance(lines, (tuple, list)):
        Cache.set('ll', lines)
        lines = apply_line_filters_and_sorting(
            lines, sort_by, group_id, required_filter)

        Cache.set('last_choosed_ll', lines)

        tab = tabulate([list(l.values()) for l in lines],
                       headers=['Id', 'Name', 'Lat', 'Lng',
                                'In maintenance', 'Groups'])
        Cache.set('last_showed_ll', tab)
        Cache.set('last_showed', tab)
        print(tab)
    else:
        print(lines)


def ll_status(args):
    lines = Cache.get('ll')
    if lines is None:
        lines = _get_lines()

    lines_status = _get_lines_status()
    lines_status_by_id = {l_st['line_id']: l_st for l_st in lines_status}

    sort_by = args['--sort'] or 'id'
    group_id = args['--group']
    required_filter = args['--filter']

    if isinstance(lines_status, (tuple, list)):
        Cache.set('ll', lines)
        lines = apply_line_filters_and_sorting(
            lines, sort_by, group_id, required_filter)

        Cache.set('last_choosed_ll', lines)

        for line in lines:
            del line['lat']
            del line['lng']
            del line['groups']

            line_st = lines_status_by_id.get(line['id'], {})
            el_params = line_st.get('el_params', {}).get('val', {}) or {}
            pwr_actv = el_params.get('pwr_actv', ['-'])[0]
            if isinstance(pwr_actv, float):
                pwr_actv = round(pwr_actv, 3)

            eap = el_params.get('eap', ['-'])[0]
            if isinstance(eap, float):
                eap = round(eap, 3)

            line.update(dict(
                relay=line_st.get('relay', {}).get('val', '-'),
                ctl_mode=line_st.get('ctl_mode', {}).get('val', '-'),
                pwr_actv=pwr_actv, eap=eap))

        tab = tabulate([list(l.values()) for l in lines],
                       headers=['Id', 'Name', 'In maintenance', 'Relay',
                                'Ctl. mode', 'Pwr. Active', 'Energy Sum.'])
        Cache.set('last_showed_ll_st', tab)
        Cache.set('last_showed', tab)
        print(tab)
    else:
        print(lines_status)


def ll_groups_list(args):
    groups = _get_line_groups()

    if isinstance(groups, (tuple, list)):
        groups = sorted(groups, key=lambda g: g['group_id'])
        Cache.set('last_choosed_llgr', groups)

        tab = tabulate([
            [v for k, v in gr.items() if k != 'lines'] for gr in groups
        ], headers=['Id', 'Name', 'Child groups'])

        Cache.set('last_showed_llgr', tab)
        Cache.set('last_showed', tab)
        print(tab)

    else:
        print(groups)


def ll_show(args):
    lines = Cache.get('ll')
    id_ = args['<id>']

    if lines is None:
        lines = _get_lines()

    if isinstance(lines, (tuple, list)):
        choose, *_ = tuple(filter(lambda l: l['id'].startswith(id_), lines))
        line = _get_line(choose['id'])
        line.update(_get_line_state(choose['id']))
        pprint(line)


def show_last(args):
    nothing = 'Nothing to show'
    if args['<show>'] == '_':
        print(Cache.get('last_showed', nothing))

    elif args['<show>'] == 'll':
        print(Cache.get('last_showed_ll', nothing))

    elif args['<show>'] == 'll_st':
        print(Cache.get('last_showed_ll_st', nothing))

    elif args['<show>'] == 'llgr':
        print(Cache.get('last_showed_llgr', nothing))


async def _auth(ws) -> dict:
    await ws.send(dumps({
        "ver": 1,
        "proto": "authReq",
        "ts": utc_ts(),
        "sid": sid(),
        "authToken": Config.get('api.ws.auth.token')
    }))
    return loads(await ws.recv())


async def lines_relay_cmd(lines: Sequence[dict], relay_state: str):
    relay_state = relay_state.lower()
    assert relay_state in ('on', 'off')

    uri = f"{ws_uri_from_url(Config.get('api.url'))}/ws"
    async with websockets.connect(uri) as ws:
        print(await _auth(ws))
        await ws.send(dumps({
            "ver": 1,
            "proto": "objSendCtlCmdReq",
            "ts": utc_ts(),
            "sid": sid(),
            "cmd": "relayEnable" if relay_state == 'on' else 'relayDisable',
            "cmdParams": {},
            "objects": [
                {
                    "id": line['id'],
                    "type": "line",
                } for line in lines
            ]
          }
        ))
        print(loads(await ws.recv()))


async def lines_mode_cmd(lines: Sequence[dict], mode: str):
    assert mode in ('auto:sch', 'manual')

    uri = f"{ws_uri_from_url(Config.get('api.url'))}/ws"
    async with websockets.connect(uri) as ws:
        print(await _auth(ws))
        await ws.send(dumps({
            "ver": 1,
            "proto": "setCtlModeReq",
            "ts": utc_ts(),
            "sid": sid(),
            "objects": [
                {
                    "id": line['id'],
                    "type": "line",
                    "mode": mode
                } for line in lines
            ]
          }
        ))
        print(loads(await ws.recv()))


def ll_cmd(args):
    lines = Cache.get('last_choosed_ll')
    if lines is None:
        print('No lines choosed')
        return

    if args['--relay'] is not None:
        resp = input(
            f'Set relay {args["--relay"]} for {len(lines)} lines? [y/N]: ')

        if resp.lower() == 'y':
            get_event_loop().run_until_complete(
                lines_relay_cmd(lines, args['--relay']))

    if args['--mode'] is not None:
        resp = input(
            f'Set mode {args["--mode"]} for {len(lines)} lines? [y/N]: ')

        if resp.lower() == 'y':
            get_event_loop().run_until_complete(
                lines_mode_cmd(lines, args['--mode']))


def determine_command(commands: OrderedDict, arguments: dict):
    for cmd in commands:
        if all(arguments.get(token, False) for token in cmd.split('.')):
            return commands[cmd]


def start_interactive():
    while True:
        command = input('> ')
        if command in ('exit', 'quit', '\q'):
            return

        try:
            arguments = docopt(
                __doc__,
                argv=command.split(),
                version=pkg_resources.get_distribution(
                    'uspherum-esc-cli').version
            )
            dispatch_command(arguments, is_interactive=True)
        except DocoptExit:
            print(printable_usage(__doc__))


def dispatch_command(arguments, is_interactive=False):
    commands = OrderedDict({
        'config.list': config_list,
        'config.set': config_set,
        'll.list': ll_list,
        'll.status': ll_status,
        'll.show': ll_show,
        'll.cmd': ll_cmd,
        'llgr.list': ll_groups_list,
        'show': show_last
    })

    command = determine_command(commands, arguments)
    if command is not None:
        command(arguments)
    else:
        print('Unknown command requested!', printable_usage(__doc__))


def main():
    arguments = docopt(
        __doc__,
        version=pkg_resources.get_distribution('uspherum-esc-cli').version
    )

    # Интерактивный режим с использование WebSocket
    if arguments['-i'] or arguments['--interactive']:
        start_interactive()
        return

    dispatch_command(arguments)


if __name__ == '__main__':
    main()
