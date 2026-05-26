{% extends "base.html" %}
{% block title %}{{ 'Editar' if project else 'Novo' }} projeto{% endblock %}
{% block content %}

<div class="page-header">
  <div class="page-title-wrap">
    <h1>{{ 'Editar' if project else 'Novo' }} projeto</h1>
    <p>Defina uma meta financeira e quem participa.</p>
  </div>
  <a href="{{ url_for('projects.list_projects') }}" class="btn btn-ghost">← Voltar</a>
</div>

<div class="card" style="max-width:780px;">
  <form method="POST">

    <div class="form-group">
      <label class="form-label">Nome do projeto *</label>
      <input class="form-control" name="name" required
             value="{{ project.name if project }}"
             placeholder="ex: Viagem Europa 2027, Fundo de Emergência, Reforma do banheiro">
    </div>

    <div class="form-group">
      <label class="form-label">Descrição</label>
      <textarea class="form-control" name="description"
                placeholder="Por que esse objetivo importa?">{{ project.description if project }}</textarea>
    </div>

    <div class="form-row">
      <div class="form-group">
        <label class="form-label">Meta (R$) *</label>
        <input class="form-control mono" name="target_amount" required inputmode="decimal"
               value="{{ project.target_amount if project }}"
               placeholder="0,00">
      </div>
      <div class="form-group">
        <label class="form-label">Prazo</label>
        <input class="form-control" type="date" name="deadline"
               value="{{ project.deadline.isoformat() if project and project.deadline else '' }}">
        <div class="form-help">Opcional. Para acompanhar o ritmo.</div>
      </div>
    </div>

    <div class="divider"></div>
    <h3>Aporte automático mensal</h3>
    <p class="text-dim text-small mb-2">
      Configure cotas mensais para cada membro. No dia escolhido, o sistema lança automaticamente
      um aporte com o valor da cota de cada um.
    </p>

    <div class="form-row">
      <div class="form-group">
        <label class="form-label">Total mensal (soma das cotas)</label>
        <input class="form-control mono" name="monthly_auto" inputmode="decimal"
               value="{{ project.monthly_auto if project }}" placeholder="0,00">
        <div class="form-help">Deixe zerado para desabilitar.</div>
      </div>
      <div class="form-group">
        <label class="form-label">Dia do lançamento</label>
        <input class="form-control" type="number" name="auto_day" min="1" max="28"
               value="{{ project.auto_day if project else 1 }}">
        <div class="form-help">De 1 a 28.</div>
      </div>
    </div>

    <div class="divider"></div>
    <h3>Membros</h3>
    <p class="text-dim text-small mb-2">
      Selecione quem participa do projeto. Defina a cota mensal de cada um (usada no aporte automático).
    </p>

    <div style="background:var(--bg);border-radius:var(--radius);padding:14px;">
      <!-- O próprio usuário (owner ao criar) -->
      {% set my_share = 0 %}
      {% if project %}
        {% for m in project.members %}{% if m.user_id == current_user.id %}{% set my_share = m.monthly_share %}{% endif %}{% endfor %}
      {% endif %}
      <div class="share-row">
        <div>
          {% if current_user.photo %}
            <img src="{{ current_user.photo_url }}" class="avatar" style="width:28px;height:28px;">
          {% else %}
            <div class="avatar-fallback" style="width:28px;height:28px;font-size:0.78rem;">{{ current_user.full_name[0]|upper }}</div>
          {% endif %}
        </div>
        <div>
          <strong>{{ current_user.full_name }}</strong>
          <span class="text-faint text-small">(você {% if not project %}· proprietário{% endif %})</span>
        </div>
        <input class="form-control mono" name="member_share_{{ current_user.id }}"
               inputmode="decimal" placeholder="Cota mensal" value="{{ my_share }}">
      </div>

      {% for u in users %}
        {% set is_member = False %}
        {% set u_share = 0 %}
        {% if project %}
          {% for m in project.members %}
            {% if m.user_id == u.id %}{% set is_member = True %}{% set u_share = m.monthly_share %}{% endif %}
          {% endfor %}
        {% endif %}
        <div class="share-row">
          <div>
            <label class="form-check" style="padding:0;background:none;border:none;">
              <input type="checkbox" name="member_{{ u.id }}" {% if is_member %}checked{% endif %}>
            </label>
          </div>
          <div class="flex flex-gap">
            {% if u.photo %}
              <img src="{{ u.photo_url }}" class="avatar" style="width:28px;height:28px;">
            {% else %}
              <div class="avatar-fallback" style="width:28px;height:28px;font-size:0.78rem;">{{ u.full_name[0]|upper }}</div>
            {% endif %}
            <strong>{{ u.full_name }}</strong>
          </div>
          <input class="form-control mono" name="member_share_{{ u.id }}"
                 inputmode="decimal" placeholder="Cota mensal" value="{{ u_share }}">
        </div>
      {% endfor %}
    </div>

    <div class="divider"></div>
    <div class="flex flex-gap">
      <button class="btn btn-primary">Salvar projeto</button>
      <a href="{{ url_for('projects.list_projects') }}" class="btn btn-ghost">Cancelar</a>
    </div>
  </form>
</div>

{% endblock %}
