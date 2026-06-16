# wisdom_inspect3.py
# Busca entradas cuya clave (word) empieza por hiragana o kanji
from readmdict import MDX
import json
import re

print("Inspeccionando Wisdom (buscando entradas japonesas)...")
mdx = MDX("./dictionaries/三省堂 ウィズダム和英辞典 第3版/SANWIZJ3.mdx")
items = list(mdx.items())
print(f"Total entradas: {len(items)}")

# Regex: empieza por hiragana, katakana o kanji
JP_START = re.compile(r'^[\u3040-\u9FFF]')

muestras = []
for word, definition in items:
    try:
        w = word.decode("utf-8")
        d = definition.decode("utf-8")

        if not JP_START.match(w):
            continue
        if d.startswith("@@@LINK"):
            continue
        if "<img src=" in d and len(d) < 400:
            continue

        muestras.append({"word": w, "definition": d})
        if len(muestras) >= 10:
            break
    except:
        pass

with open("wisdom_samples3.json", "w", encoding="utf-8") as f:
    json.dump(muestras, f, ensure_ascii=False, indent=2)

print(f"Guardadas {len(muestras)} entradas japonesas → wisdom_samples3.json")