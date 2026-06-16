# 1. Crear y entrar en la carpeta
cd PATH/to/orchestrator-service

# 2. Crear entorno virtual (Aísla las librerías de este servicio)
python3 -m venv venv

# 3. Activar el entorno
source venv/bin/activate

# 4. Actualizar herramientas base
pip install --upgrade pip

# 5. Instalar las dependencias específicas de este servicio
# (Para el orquestador solo necesitamos FastAPI, Uvicorn y HTTPX)
pip install fastapi uvicorn httpx

# 6. Guardar la configuración para el futuro (Docker)
pip freeze > requirements.txt