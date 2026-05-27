{% extends "base.html" %}
{% block title %}Entrar{% endblock %}
{% block public %}
<div class="login-page">
  <div class="login-art login-art-photo" style="background-image: linear-gradient(180deg, rgba(26,22,18,0.25) 0%, rgba(26,22,18,0.15) 35%, rgba(26,22,18,0.75) 75%, rgba(26,22,18,0.95) 100%), url('{{ url_for('static', filename='img/hero.jpg') }}');">
    <div>
      <div class="brand">{{ app_name }}</div>
      <div class="brand-sub">o nosso, do nosso jeito</div>
    </div>
    <div style="margin-top: auto;">
      <h1 class="login-art-title">A nossa<br>vida financeira,<br><em>juntos.</em></h1>
      <p class="login-art-quote mt-3">
        Renda, gastos e sonhos compartilhados em um só lugar — porque o que é
        nosso merece um cantinho à altura.
      </p>
      <div class="text-faint text-small mt-3">© {{ current_year }} · nosso espaço</div>
    </div>
  </div>

  <div class="login-form-wrap">
    <form method="POST" class="login-form">
      <h2>Bem-vindo de volta</h2>
      <p class="subtitle">Entre com suas credenciais para continuar.</p>

      <div class="form-group">
        <label class="form-label">Usuário</label>
        <input class="form-control" name="username" required autofocus>
      </div>

      <div class="form-group">
        <label class="form-label">Senha</label>
        <input class="form-control" name="password" type="password" required>
      </div>

      <button class="btn btn-primary w-100" type="submit" style="justify-content:center;">
        Entrar
      </button>

      <p class="text-faint text-small text-center mt-3">
        Não tem conta? Peça para o admin da casa cadastrar você.
      </p>
    </form>
  </div>
</div>
{% endblock %}
