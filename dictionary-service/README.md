## 🛠️ Dictionary Service Setup

Follow these steps to initialize and run the Japanese dictionary microservice on your Mac.

### 1. Environment Initialization

Open your Mac Terminal and run the following commands:

# Move into the dictionary service directory
cd PATH/to/dictionary-service

# Create the Python virtual environment
python3 -m venv venv

# Activate the virtual environment
source venv/bin/activate

# Upgrade pip to the latest version
pip install --upgrade pip

# Install required dependencies (FastAPI & Sudachi Rust)
pip install fastapi uvicorn sudachipy sudachidict_core

# Freeze dependencies for future Docker integration
pip freeze > requirements.txt