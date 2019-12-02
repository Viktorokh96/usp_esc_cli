"""
uSpherum ESC Console Client

Usage:
    cli.py config list
    cli.py config set <config_name> <value>
    cli.py ll list
    cli.py ll show <id>
    cli.py ll add <name> [--lat <lat>] [--lng <lng>]

Options:
    -h --help   Show this help screen
    --lat       Object latitude
    --lng       Object longitude
"""
from json import load, dump
from uuid import UUID
import pkg_resources
import pickle
from os.path import dirname, join, exists
from collections import OrderedDict, abc
from urllib.parse import urljoin
from pprint import pprint
from jsonschema import validate
from docopt import docopt
from tabulate import tabulate
import requests


def deep_update(d, u):
    for k, v in u.items():
        if isinstance(v, abc.Mapping):
            d[k] = deep_update(d.get(k, {}), v)
        else:
            d[k] = v
    return d


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
            dump(cfg, f)


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
    def get(cls, key):
        return cls._current_cache().get(key)

    @classmethod
    def set(cls, key, value):
        current_cache = cls._current_cache()
        current_cache[key] = value
        with open(cls.cache_path, 'wb') as cache_file:
            pickle.dump(current_cache, cache_file)


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


def ll_list(args):
    lines = _get_lines()
    if isinstance(lines, (tuple, list)):
        lines = sorted(lines, key=lambda l: UUID(l['id']))
        Cache.set('ll', lines)

        print(tabulate([list(l.values()) for l in lines],
                       headers=['Id', 'Name', 'Lat', 'Lng']))
    else:
        print(lines)


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


def determine_command(commands: OrderedDict, arguments: dict):
    for cmd in commands:
        if all(arguments.get(token, False) for token in cmd.split('.')):
            return commands[cmd]


def main():
    arguments = docopt(
        __doc__,
        version=pkg_resources.get_distribution('uspherum-esc-cli').version
    )

    commands = OrderedDict({
        'config.list': config_list,
        'config.set': config_set,
        'll.list': ll_list,
        'll.show': ll_show
    })

    command = determine_command(commands, arguments)
    if command is not None:
        command(arguments)
    else:
        print('Unknown command requested!', __doc__)


if __name__ == '__main__':
    main()
