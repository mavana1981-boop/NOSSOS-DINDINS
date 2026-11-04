"""
Aviso único do app: grava no sininho, manda push para os aparelhos do usuário
e (se o SMTP estiver configurado no Railway) manda e-mail.
"""
import os


def nao_lidas(user_id: int) -> int:
    from app.models import Notificacao
    return Notificacao.query.filter_by(user_id=user_id, lida=False).count()


def notificar(user_id: int, titulo: str, mensagem: str = "", link: str = None,
              tag: str = None, email: bool = True) -> None:
    from app import db
    from app.models import Notificacao, User

    db.session.add(Notificacao(user_id=user_id, titulo=titulo, mensagem=mensagem, link=link))
    db.session.commit()

    try:
        from app.webpush import enviar_para_usuario
        enviar_para_usuario(user_id, {"title": titulo, "body": mensagem or "",
                                      "url": link or "/notificacoes", "tag": tag})
    except Exception as e:
        print(f"[notificar] push falhou para usuário {user_id}: {e}")

    if email:
        try:
            from app.agente_andamento import enviar_email
            u = User.query.get(user_id)
            destino = os.environ.get("ALERTA_EMAIL_TO") or (u.email if u else None)
            base = os.environ.get("APP_URL", "").rstrip("/")
            corpo = (mensagem or "") + (f"\n\nAbrir: {base}{link}" if base and link else "")
            enviar_email(destino, titulo, corpo + "\n\n— Nosso Dindin")
        except Exception as e:
            print(f"[notificar] e-mail falhou para usuário {user_id}: {e}")
