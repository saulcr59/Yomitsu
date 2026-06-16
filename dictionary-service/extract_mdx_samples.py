# extract_mdx_samples.py
# Ejecutar: python extract_mdx_samples.py
# Requiere: pip install readmdict

from readmdict import MDX
import json
import os

diccionarios = {
    "kenkyusha": "研究社和英大辞典/研究社新和英大辞典.mdx",
    "genius":    "大修館 ジーニアス和英辞典 第3版/GENIUSJ3.mdx",
    "wisdom":    "三省堂 ウィズダム和英辞典 第3版/SANWIZJ3.mdx",
    "japan_times": "The Japan Times - Dictionary of Japanese Grammar (Jpn-Eng-Jpn) (MDX)/(The Japan Times) A Dictionary of Japanese Grammar [Complete Edition].mdx",
}

css_files = {
    "genius": "大修館 ジーニアス和英辞典 第3版/GENIUSJ3.css",
    "wisdom": "三省堂 ウィズダム和英辞典 第3版/SANWIZJ3.css",
    "japan_times": "The Japan Times - Dictionary of Japanese Grammar (Jpn-Eng-Jpn) (MDX)/dojg.css",
}

resultado = {}

for nombre, ruta in diccionarios.items():
    print(f"Procesando {nombre}...")
    try:
        mdx = MDX(f"./dictionaries/{ruta}")
        items = list(mdx.items())
        print(f"  Total entradas: {len(items)}")
        
        muestras = []
        for word, definition in items[:30]:
            try:
                muestras.append({
                    "word": word.decode("utf-8"),
                    "definition": definition.decode("utf-8")
                })
            except:
                pass
        
        resultado[nombre] = {
            "total": len(items),
            "samples": muestras
        }
        
        # CSS si existe
        if nombre in css_files and os.path.exists(f"./dictionaries/{css_files[nombre]}"):
            with open(f"./dictionaries/{css_files[nombre]}", encoding="utf-8") as f:
                resultado[nombre]["css"] = f.read()
                
    except Exception as e:
        resultado[nombre] = {"error": str(e)}

with open("mdx_samples.json", "w", encoding="utf-8") as f:
    json.dump(resultado, f, ensure_ascii=False, indent=2)

print("Listo → mdx_samples.json")