#!/usr/bin/env python3
import setuptools

setuptools.setup(
    name='jupyterhub_api',
    version='0.0.1',
    description='A management API for JupyterHub',
    license='GNU/GPLv3',
    author='Ariam Rivas, Philipp D. Rohde',
    scripts=['./Scripts/start_api.sh'],
    packages=[
        'jupyterhub_api',
        'jupyterhub_api.App'
    ],
    install_requires=[
        'flask>=3.0.0',
        'docker>=7.0.0',
        'requests'
    ],
    include_package_data=True,
    python_requires='>=3.9',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3 :: Only',
        'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
        'Operating System :: OS Independent'
    ]
)
