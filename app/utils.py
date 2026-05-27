def save_profile_photo(file_storage):
    """Converte foto de perfil para base64 e retorna data URI para salvar no banco."""
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_image(file_storage.filename):
        return None

    import base64
    from io import BytesIO

    try:
        img = Image.open(file_storage)
        img = img.convert("RGB")
        # Recorta quadrado centralizado
        w, h = img.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))
        img = img.resize((256, 256), Image.LANCZOS)

        # Salva em buffer e converte para base64
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=80, optimize=True)
        b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        print(f"Erro processando imagem: {e}")
        return None
