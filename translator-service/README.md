## 🛠️ Translator Service Setup

Follow these steps to initialize and run the translation microservice on your Mac.

### 1. Environment Initialization

Open your Mac Terminal and run the following commands:

# Move into the translator service directory
cd PATH/to/translator-service

# Create the Python virtual environment
python3 -m venv venv

# Activate the virtual environment
source venv/bin/activate

# Upgrade pip to the latest version
pip install --upgrade pip

# Install required dependencies (FastAPI, HTTPX, and Pydantic)
pip install fastapi uvicorn httpx pydantic

# Freeze dependencies for future Docker integration
pip freeze > requirements.txt