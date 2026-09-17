from setuptools import find_packages, setup


setup(
    name="cisco-device-onboard",
    version="0.1.0",
    description="Dedicated console firmware upgrade and Meraki Dashboard configuration tools",
    python_requires=">=3.6",
    package_dir={"": "src"},
    packages=find_packages("src"),
    install_requires=[
        "pexpect>=4.9",
        "requests>=2.26,<2.27; python_version < '3.7'",
        "requests>=2.31; python_version >= '3.7'",
        "PyYAML>=6.0.1,<6.0.2; python_version < '3.8'",
        "PyYAML>=6.0; python_version >= '3.8'",
        "dataclasses>=0.8; python_version < '3.7'",
    ],
    entry_points={
        "console_scripts": [
            "console-upgrade=cisco_device_onboard.console_upgrade:main",
            "dashboard-config=cisco_device_onboard.dashboard_config:main",
        ]
    },
)
