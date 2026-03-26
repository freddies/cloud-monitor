from setuptools import setup, find_packages

setup(
    name="cloud-monitor",
    version="1.0.0",
    description="AI-Powered Cloud Monitoring & Incident Detection System",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    python_requires=">=3.10",
    install_requires=[
        "flask>=3.0.0",
        "redis>=5.0.0",
        "boto3>=1.34.0",
        "numpy>=1.26.0",
        "pandas>=2.1.0",
        "scikit-learn>=1.3.0",
        "tensorflow>=2.15.0",
        "psutil>=5.9.0",
        "prometheus-client>=0.19.0",
        "APScheduler>=3.10.0",
    ],
)