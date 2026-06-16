# check_honjitsu.py
from readmdict import MDX

mdx = MDX("./dictionaries/研究社和英大辞典/研究社新和英大辞典.mdx")
items = list(mdx.items())

print(f"Total entradas: {len(items)}")

# Buscar entradas que contengan 本日 en la clave o en la definición
for word, definition in items:
    try:
        w = word.decode("utf-8")
        d = definition.decode("utf-8")
        if "本日" in w:
            print(f"EN CLAVE: '{w}'")
            print(d[:150])
            print("---")
    except:
        pass

print("Hecho")