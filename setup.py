from setuptools import setup, find_packages

"""
CLI интерфейс к сервисам ESC
"""


setup(
    name='uspherum-esc-cli',
    version='0.1.0',
    long_description=__doc__,
    packages=find_packages(),
    install_requires=[
        'requests>=2.22.0,<3',
        'tabulate>=0.8.6,<1',
        'jsonschema>=3.2.0',
        'docopt>=0.6.2',
    ],
    extras_require={
        'dev': [
            'tox>=3.9.0,<4',
            'ipython==7.5.0',
        ]
    },
    entry_points={
        'console_scripts': [
            "esc-cli = esc_cli.cli:main"
        ]
    },

    author='Ohotnikov Viktor',
    author_email='v.ohotnikov@i-sberg.net'
)
